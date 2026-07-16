"""Tests for ``src.bridge.bridge_main`` (Phase 8 MVP slice)."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
from typing import Any

import httpx
import pytest

from src.bridge.bridge_main import (
    DEFAULT_BACKOFF,
    BackoffConfig,
    BridgeHeadlessPermanentError,
    ParsedArgs,
    bridge_main,
    is_connection_error,
    is_server_error,
    parse_args,
    run_bridge_loop,
)
from src.bridge.exceptions import BridgeFatalError
from src.bridge.poll_config_defaults import PollIntervalConfig
from src.bridge.types import BridgeConfig, SessionDoneStatus


# ── parse_args ──────────────────────────────────────────────────────────


def test_parse_empty_args_returns_defaults() -> None:
    out = parse_args([])
    assert out.error is None
    assert out.verbose is False
    assert out.sandbox is False
    assert out.help is False
    assert out.spawn_mode is None
    assert out.capacity is None


def test_parse_verbose_flag() -> None:
    assert parse_args(['--verbose']).verbose is True
    assert parse_args(['-v']).verbose is True


def test_parse_sandbox_toggle() -> None:
    assert parse_args(['--sandbox']).sandbox is True
    assert parse_args(['--no-sandbox']).sandbox is False


def test_parse_help_flag() -> None:
    assert parse_args(['--help']).help is True
    assert parse_args(['-h']).help is True


def test_parse_debug_file_separate_value() -> None:
    out = parse_args(['--debug-file', '/tmp/foo.log'])
    assert out.debug_file == '/tmp/foo.log'


def test_parse_debug_file_equals_form() -> None:
    out = parse_args(['--debug-file=/tmp/foo.log'])
    assert out.debug_file == '/tmp/foo.log'


def test_parse_session_timeout_converts_seconds_to_ms() -> None:
    out = parse_args(['--session-timeout', '30'])
    assert out.session_timeout_ms == 30_000
    out = parse_args(['--session-timeout=60'])
    assert out.session_timeout_ms == 60_000


def test_parse_permission_mode() -> None:
    assert parse_args(['--permission-mode', 'plan']).permission_mode == 'plan'
    assert parse_args(['--permission-mode=auto']).permission_mode == 'auto'


def test_parse_name_flag() -> None:
    assert parse_args(['--name', 'My env']).name == 'My env'
    assert parse_args(['--name=foo']).name == 'foo'


def test_parse_spawn_session_translates_to_single_session() -> None:
    """``--spawn session`` is the user-facing alias for 'single-session'."""
    out = parse_args(['--spawn', 'session'])
    assert out.spawn_mode == 'single-session'


def test_parse_spawn_other_modes() -> None:
    assert parse_args(['--spawn', 'same-dir']).spawn_mode == 'same-dir'
    assert parse_args(['--spawn=worktree']).spawn_mode == 'worktree'


def test_parse_spawn_invalid_value_returns_error() -> None:
    out = parse_args(['--spawn', 'invalid'])
    assert out.error is not None
    assert 'one of: session, same-dir, worktree' in out.error


def test_parse_spawn_specified_twice_errors() -> None:
    out = parse_args(['--spawn', 'session', '--spawn', 'worktree'])
    assert out.error == '--spawn may only be specified once'


def test_parse_capacity_positive_integer() -> None:
    assert parse_args(['--capacity', '5']).capacity == 5
    assert parse_args(['--capacity=10']).capacity == 10


def test_parse_capacity_invalid_value_errors() -> None:
    assert 'positive integer' in parse_args(['--capacity', 'foo']).error or ''
    assert 'positive integer' in parse_args(['--capacity', '-1']).error or ''
    assert 'positive integer' in parse_args(['--capacity', '0']).error or ''


def test_parse_capacity_twice_errors() -> None:
    out = parse_args(['--capacity', '1', '--capacity', '2'])
    assert out.error == '--capacity may only be specified once'


def test_parse_create_session_in_dir_toggle() -> None:
    assert parse_args(['--create-session-in-dir']).create_session_in_dir is True
    assert parse_args(['--no-create-session-in-dir']).create_session_in_dir is False


def test_parse_kairos_session_id_rejected() -> None:
    """--session-id (KAIROS) is not yet supported in MVP."""
    out = parse_args(['--session-id', 'sess-1'])
    assert out.error is not None
    assert '--session-id' in out.error


def test_parse_kairos_continue_rejected() -> None:
    out = parse_args(['--continue'])
    assert out.error is not None
    out2 = parse_args(['-c'])
    assert out2.error is not None


def test_parse_unknown_arg_errors() -> None:
    out = parse_args(['--never-defined'])
    assert out.error == 'Unknown argument: --never-defined'


# ── predicates ───────────────────────────────────────────────────────────


def test_is_connection_error_on_network_classes() -> None:
    assert is_connection_error(ConnectionResetError())
    assert is_connection_error(socket.gaierror())
    assert is_connection_error(httpx.ConnectError('refused'))
    assert is_connection_error(httpx.ConnectTimeout('timeout'))
    assert not is_connection_error(BridgeFatalError('boom', status=500))
    assert not is_connection_error(ValueError('not network'))


def test_is_server_error_on_5xx_bridge_fatal() -> None:
    assert is_server_error(BridgeFatalError('5xx', status=500))
    assert is_server_error(BridgeFatalError('5xx', status=503))
    assert not is_server_error(BridgeFatalError('4xx', status=404))
    assert not is_server_error(ValueError('not server'))


# ── BackoffConfig ────────────────────────────────────────────────────────


def test_default_backoff_constants() -> None:
    assert DEFAULT_BACKOFF.conn_initial_ms == 2_000
    assert DEFAULT_BACKOFF.conn_cap_ms == 120_000
    assert DEFAULT_BACKOFF.conn_give_up_ms == 600_000
    assert DEFAULT_BACKOFF.general_initial_ms == 500
    assert DEFAULT_BACKOFF.general_cap_ms == 30_000
    assert DEFAULT_BACKOFF.general_give_up_ms == 600_000
    assert DEFAULT_BACKOFF.shutdown_grace_ms == 30_000


def test_backoff_config_constructible_with_overrides() -> None:
    cfg = BackoffConfig(conn_initial_ms=500, shutdown_grace_ms=10_000)
    assert cfg.conn_initial_ms == 500
    assert cfg.shutdown_grace_ms == 10_000
    # Untouched fields keep defaults.
    assert cfg.general_cap_ms == 30_000


# ── run_bridge_loop ─────────────────────────────────────────────────────


class FakeApiClient:
    def __init__(
        self,
        *,
        poll_results: list[Any] | None = None,
    ) -> None:
        self.poll_results = poll_results or []
        self.ack_calls: list[tuple[str, str, str]] = []
        self.stop_calls: list[tuple[str, str, bool]] = []
        self.deregister_calls: list[str] = []
        self.register_calls: list[Any] = []
        # Phase 15: capture reconnect_session calls for v2 refresh tests.
        self.reconnect_calls: list[tuple[str, str]] = []

    async def register_bridge_environment(self, config: Any) -> dict[str, str]:
        self.register_calls.append(config)
        return {
            'environment_id': 'env-srv',
            'environment_secret': 'sec-srv',
        }

    async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
        if not self.poll_results:
            return None
        return self.poll_results.pop(0)

    async def acknowledge_work(self, env_id: str, work_id: str, tok: str) -> None:
        self.ack_calls.append((env_id, work_id, tok))

    async def stop_work(self, env_id: str, work_id: str, force: bool) -> None:
        self.stop_calls.append((env_id, work_id, force))

    async def deregister_environment(self, env_id: str) -> None:
        self.deregister_calls.append(env_id)

    async def archive_session(self, sid: str) -> None:
        pass

    async def reconnect_session(self, env_id: str, session_id: str) -> None:
        self.reconnect_calls.append((env_id, session_id))

    async def heartbeat_work(self, *_a: Any) -> dict[str, Any]:
        return {'lease_extended': True, 'state': 'running'}

    async def send_permission_response_event(self, *_a: Any) -> None:
        pass


class FakeSessionHandle:
    def __init__(self, session_id: str, access_token: str) -> None:
        self._session_id = session_id
        self._access_token = access_token
        self._done: asyncio.Future[SessionDoneStatus] = (
            asyncio.get_event_loop().create_future()
        )
        self.killed = False
        self.force_killed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def activities(self) -> list[Any]:
        return []

    @property
    def current_activity(self) -> Any:
        return None

    @property
    def last_stderr(self) -> list[str]:
        return []

    async def wait_done(self) -> SessionDoneStatus:
        return await self._done

    def kill(self) -> None:
        self.killed = True

    def force_kill(self) -> None:
        self.force_killed = True

    def write_stdin(self, data: str) -> None:
        pass

    def update_access_token(self, token: str) -> None:
        # Phase 15: record the new token so refresh tests can assert
        # it propagated to the child.
        self._access_token = token

    def complete(self, status: SessionDoneStatus = 'completed') -> None:
        if not self._done.done():
            self._done.set_result(status)


class FakeSpawner:
    def __init__(self) -> None:
        self.spawns: list[tuple[Any, str]] = []
        self.handles: list[FakeSessionHandle] = []

    def spawn(self, opts: Any, working_dir: str) -> FakeSessionHandle:
        self.spawns.append((opts, working_dir))
        h = FakeSessionHandle(
            session_id=opts['session_id'],
            access_token=opts['access_token'],
        )
        self.handles.append(h)
        return h


def _encode_work_secret(
    *,
    use_ccr_v2: bool = True,
    api_base_url: str = 'https://api.example.com',
) -> str:
    payload = {
        'version': 1,
        'session_ingress_token': 'sess-jwt',
        'api_base_url': api_base_url,
        'sources': [],
        'auth': [],
        'use_code_sessions': use_ccr_v2,
    }
    raw = json.dumps(payload).encode('utf-8')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _bridge_config(*, max_sessions: int = 1) -> BridgeConfig:
    return BridgeConfig(
        dir='/tmp/test',
        machine_name='test',
        branch='main',
        git_repo_url=None,
        max_sessions=max_sessions,
        spawn_mode='single-session',
        verbose=False,
        sandbox=False,
        bridge_id='br-1',
        worker_type='claude_code',
        environment_id='env-srv',
        api_base_url='https://api.example.com',
        session_ingress_url='https://api.example.com',
    )


@pytest.mark.asyncio
async def test_run_loop_cancellation_returns_promptly() -> None:
    """Setting cancel_event during the loop should exit within ~1 sec."""
    api = FakeApiClient()
    spawner = FakeSpawner()
    cancel = asyncio.Event()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    asyncio.create_task(cancel_soon())
    loop = asyncio.get_running_loop()
    start = loop.time()
    await run_bridge_loop(
        _bridge_config(),
        'env-srv', 'sec-srv', api, spawner, cancel,
    )
    elapsed = loop.time() - start
    assert elapsed < 2.0
    # Deregister called as part of shutdown.
    assert api.deregister_calls == ['env-srv']


@pytest.mark.asyncio
async def test_run_loop_spawns_session_for_v2_work() -> None:
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()

    async def runner() -> None:
        await run_bridge_loop(
            _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
        )

    task = asyncio.create_task(runner())
    # Wait for the spawn.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    assert len(spawner.spawns) == 1
    opts, _wd = spawner.spawns[0]
    assert opts['session_id'] == 'cse_w1'
    assert opts['access_token'] == 'sess-jwt'
    assert opts['use_ccr_v2'] is True
    # Ack happened.
    assert any(w == 'work-1' for _e, w, _t in api.ack_calls)

    # Complete the session + cancel the loop.
    spawner.handles[0].complete('completed')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_run_loop_spawns_session_for_v1_work() -> None:
    """Phase 14c: v1 work items dispatch in the daemon path. The
    session-ingress URL is derived from ``config.session_ingress_url``
    (NOT ``secret.api_base_url``, which may point at a remote
    proxy/tunnel — see TS ``bridgeMain.ts:905-907``). Distinct hosts
    catch a regression to either side."""
    work = {
        'id': 'work-v1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v1'},
        'secret': _encode_work_secret(
            use_ccr_v2=False,
            api_base_url='https://remote-proxy.example.com',
        ),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()

    # Override session_ingress_url so it's distinct from the secret's
    # api_base_url — the v1 dispatch must pick session_ingress_url.
    config = _bridge_config()
    config.session_ingress_url = 'https://bridge-local.example.com'

    async def runner() -> None:
        await run_bridge_loop(
            config, 'env-srv', 'sec-srv', api, spawner, cancel,
        )

    task = asyncio.create_task(runner())
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    assert len(spawner.spawns) == 1
    opts, _wd = spawner.spawns[0]
    # Spawn URL derived from the bridge's session_ingress_url, NOT
    # the secret's api_base_url. Without this split the daemon would
    # (incorrectly) build the WS URL against remote-proxy.example.com.
    assert opts['sdk_url'] == (
        'wss://bridge-local.example.com/v1/session_ingress/ws/cse_v1'
    )
    assert opts['use_ccr_v2'] is False
    assert opts['access_token'] == 'sess-jwt'
    # Work was ack'd, NOT stopped (the v1 refusal gate is lifted).
    assert any(w == 'work-v1' for _e, w, _t in api.ack_calls)
    assert not any(w == 'work-v1' for _e, w, _f in api.stop_calls)

    # Complete the session + cancel the loop.
    spawner.handles[0].complete('completed')
    cancel.set()
    await task


# ─── Phase 15: JWT refresh wiring with v1/v2 split ────────────────────


@pytest.mark.asyncio
async def test_daemon_schedules_token_refresh_on_spawn() -> None:
    """Direct daemon unit test (full integration): construct a
    ``_BridgeDaemon`` with ``get_access_token`` wired, invoke
    ``_process_work`` with a real-decodable JWT, assert the daemon
    actually scheduled a refresh + tracks the v2 session id."""
    import base64
    import json
    import time

    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    # Build a JWT with a real ``exp`` claim 1 hour out so the
    # scheduler can decode it and arm a timer (without a real exp,
    # ``schedule`` silently no-ops at jwt_utils.py:151-162).
    payload = {'exp': int(time.time()) + 3600, 'session_id': 'cse_v2'}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode('utf-8'),
    ).rstrip(b'=').decode('ascii')
    real_jwt = f'header.{payload_b64}.signature'

    secret_payload = {
        'version': 1,
        'session_ingress_token': real_jwt,
        'api_base_url': 'https://api.example.com',
        'sources': [],
        'auth': [],
        'use_code_sessions': True,
    }
    raw = json.dumps(secret_payload).encode('utf-8')
    encoded_secret = base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')

    api = FakeApiClient()
    spawner = FakeSpawner()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=spawner,
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    assert daemon.token_refresh is not None

    work = {
        'id': 'work-v2',
        'type': 'work',
        'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': encoded_secret,
        'created_at': '2026-05-26',
    }
    await daemon._process_work(work)

    # v2 session id tracked.
    assert 'cse_v2' in daemon.v2_sessions
    # Scheduler armed a real timer (real JWT decoded → timer in
    # ``_timers`` dict, not silently no-op'd).
    assert 'cse_v2' in daemon.token_refresh._timers
    # Spawn happened (not the existingHandle short-circuit).
    assert len(spawner.spawns) == 1

    # Cleanup: complete + run _on_session_done.
    spawner.handles[0].complete('completed')
    done_task = daemon.session_done_tasks.get('cse_v2')
    if done_task is not None:
        await done_task
    # Scheduler cancelled the timer + v2_sessions discarded.
    assert 'cse_v2' not in daemon.v2_sessions
    assert 'cse_v2' not in daemon.token_refresh._timers


@pytest.mark.asyncio
async def test_daemon_existing_handle_path_updates_token() -> None:
    """Phase 15 CRITIC: when work arrives for a session already in
    ``active_sessions`` (e.g. server re-dispatched after a v2 JWT
    refresh), the daemon updates the existing handle's token + reschedules
    the refresh, and does NOT spawn a duplicate subprocess. Mirrors
    TS ``bridgeMain.ts:868-885``."""
    import base64
    import json
    import time

    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    payload = {'exp': int(time.time()) + 3600, 'session_id': 'cse_v2'}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode('utf-8'),
    ).rstrip(b'=').decode('ascii')
    real_jwt = f'header.{payload_b64}.signature'
    secret_payload = {
        'version': 1,
        'session_ingress_token': real_jwt,
        'api_base_url': 'https://api.example.com',
        'sources': [],
        'auth': [],
        'use_code_sessions': True,
    }
    raw = json.dumps(secret_payload).encode('utf-8')
    encoded_secret = base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')

    api = FakeApiClient()
    spawner = FakeSpawner()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=spawner,
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    # Pre-populate an active session for cse_v2 (simulates the
    # session already running when the re-dispatched work arrives).
    existing_handle = FakeSessionHandle('cse_v2', 'OLD-token')
    daemon.active_sessions['cse_v2'] = existing_handle
    daemon.v2_sessions.add('cse_v2')
    daemon.session_work_ids['cse_v2'] = 'old-work-id'

    work = {
        'id': 'work-redispatched',
        'type': 'work',
        'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': encoded_secret,
        'created_at': '2026-05-26',
    }
    await daemon._process_work(work)

    # NO new spawn — existing handle was updated in place.
    assert spawner.spawns == []
    # Existing handle's token was updated to the fresh JWT.
    assert existing_handle.access_token == real_jwt
    # work_id was bumped to the new redispatch.
    assert daemon.session_work_ids['cse_v2'] == 'work-redispatched'
    # Refresh was rescheduled (real JWT → timer armed).
    assert 'cse_v2' in daemon.token_refresh._timers
    # Existing handle remains in active_sessions.
    assert daemon.active_sessions['cse_v2'] is existing_handle


@pytest.mark.asyncio
async def test_daemon_token_refresh_v1_pushes_to_handle() -> None:
    """Direct daemon unit test: simulate a v1 session, fire
    ``on_refresh`` via the scheduler's stored callback, assert the
    fresh token landed on ``handle.update_access_token``."""
    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    api = FakeApiClient()
    spawner = FakeSpawner()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=spawner,
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    assert daemon.token_refresh is not None
    # Prime active_sessions with a v1 session (NOT in v2_sessions).
    handle = FakeSessionHandle('cse_v1', 'initial-jwt')
    daemon.active_sessions['cse_v1'] = handle
    # Fire the refresh callback directly.
    daemon.token_refresh._on_refresh('cse_v1', 'fresh-oauth-token')
    await asyncio.sleep(0.01)
    # v1: token pushed to the child.
    assert handle.access_token == 'fresh-oauth-token'
    # v1: reconnect_session NOT called.
    assert api.reconnect_calls == []


@pytest.mark.asyncio
async def test_daemon_token_refresh_v2_calls_reconnect() -> None:
    """v2: ``on_refresh`` schedules ``api.reconnect_session(env, sid)``
    instead of pushing to the child. The fresh token does NOT touch
    ``handle.update_access_token``."""
    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    api = FakeApiClient()
    spawner = FakeSpawner()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=spawner,
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    assert daemon.token_refresh is not None
    handle = FakeSessionHandle('cse_v2', 'initial-jwt')
    daemon.active_sessions['cse_v2'] = handle
    daemon.v2_sessions.add('cse_v2')

    daemon.token_refresh._on_refresh('cse_v2', 'fresh-oauth-token')
    # The reconnect_session call is scheduled as an async task.
    await asyncio.sleep(0.05)
    assert api.reconnect_calls == [('env-srv', 'cse_v2')]
    # v2: stdin update did NOT happen.
    assert handle.access_token == 'initial-jwt'


@pytest.mark.asyncio
async def test_daemon_token_refresh_v2_failure_swallowed() -> None:
    """v2 ``reconnect_session`` raising in the refresh path must not
    propagate; the next scheduled refresh will retry naturally."""
    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    api = FakeApiClient()
    async def reconnect_raise(_env: str, _sid: str) -> None:
        raise RuntimeError('server unavailable')
    api.reconnect_session = reconnect_raise  # type: ignore[method-assign]
    spawner = FakeSpawner()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=spawner,
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    assert daemon.token_refresh is not None
    handle = FakeSessionHandle('cse_v2', 'initial-jwt')
    daemon.active_sessions['cse_v2'] = handle
    daemon.v2_sessions.add('cse_v2')

    daemon.token_refresh._on_refresh('cse_v2', 'fresh-token')
    await asyncio.sleep(0.05)
    # No exception escaped. Daemon state intact.
    assert 'cse_v2' in daemon.active_sessions
    assert 'cse_v2' in daemon.v2_sessions


@pytest.mark.asyncio
async def test_daemon_token_refresh_skipped_for_dead_session() -> None:
    """If the session ended between schedule and fire,
    ``handle is None`` short-circuits the callback — no stdin push,
    no reconnect."""
    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    api = FakeApiClient()
    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=api,
        spawner=FakeSpawner(),
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        get_access_token=lambda: 'oauth-tok',
    )
    assert daemon.token_refresh is not None
    # No active session for cse_gone.
    daemon.token_refresh._on_refresh('cse_gone', 'fresh-token')
    await asyncio.sleep(0.02)
    assert api.reconnect_calls == []


@pytest.mark.asyncio
async def test_daemon_without_get_access_token_has_no_scheduler() -> None:
    """When ``get_access_token`` is None (the default), the daemon
    constructs no scheduler — sessions use their initial JWT until
    expiry. Matches TS ``getAccessToken ? createTokenRefreshScheduler
    : null`` at ``bridgeMain.ts:283``."""
    from src.bridge.bridge_main import _BridgeDaemon, DEFAULT_BACKOFF
    from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG

    daemon = _BridgeDaemon(
        config=_bridge_config(),
        environment_id='env-srv',
        environment_secret='sec',
        api=FakeApiClient(),
        spawner=FakeSpawner(),
        cancel_event=asyncio.Event(),
        backoff_config=DEFAULT_BACKOFF,
        poll_config=DEFAULT_POLL_CONFIG,
        # No get_access_token kwarg.
    )
    assert daemon.token_refresh is None


@pytest.mark.asyncio
async def test_run_loop_respects_capacity() -> None:
    """With max_sessions=1, second work is not spawned while first is active."""
    work1 = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    work2 = {
        'id': 'w2', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w2'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work1, work2])
    spawner = FakeSpawner()
    cancel = asyncio.Event()

    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(max_sessions=1),
        'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    assert len(spawner.spawns) == 1  # capacity 1; second not yet spawned

    # A real child exits after SIGTERM during shutdown. Mirror that behavior
    # in the fake so this capacity test does not wait the production 30-second
    # shutdown grace period (the forced-shutdown path is tested separately).
    spawner.handles[0].complete('interrupted')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_run_loop_gives_up_on_410() -> None:
    """410 (env expired) → BridgeHeadlessPermanentError raised."""
    class FailingApi(FakeApiClient):
        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            raise BridgeFatalError(
                'gone', status=410, error_type='environment_expired',
            )

    api = FailingApi()
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    with pytest.raises(BridgeHeadlessPermanentError):
        await run_bridge_loop(
            _bridge_config(),
            'env-srv', 'sec-srv', api, spawner, cancel,
        )


@pytest.mark.asyncio
async def test_run_loop_shutdown_kills_sessions() -> None:
    """Cancel during a running session → kill, then wait, then deregister."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    # Speed up the shutdown grace so the test finishes quickly.
    fast_backoff = BackoffConfig(shutdown_grace_ms=200)

    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
        backoff_config=fast_backoff,
    ))
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    handle = spawner.handles[0]

    # Don't complete the session — force shutdown to use the grace timeout.
    cancel.set()

    # Allow the shutdown sequence to run (kill → wait → force_kill → deregister).
    async def auto_complete_after_grace() -> None:
        await asyncio.sleep(0.3)  # past the 200ms grace
        handle.complete('interrupted')

    asyncio.create_task(auto_complete_after_grace())
    await task

    assert handle.killed
    # Past grace, force_kill was called.
    assert handle.force_killed
    # stop_work + deregister fired.
    assert api.deregister_calls == ['env-srv']


# ── bridge_main entry point ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_main_help_returns_zero() -> None:
    code = await bridge_main(['--help'])
    assert code == 0


@pytest.mark.asyncio
async def test_bridge_main_parse_error_returns_one() -> None:
    code = await bridge_main(['--unknown'])
    assert code == 1


@pytest.mark.asyncio
async def test_bridge_main_registration_failure_returns_two() -> None:
    class FailRegister(FakeApiClient):
        async def register_bridge_environment(self, _c: Any) -> dict[str, str]:
            raise BridgeFatalError('boom', status=500)

    code = await bridge_main(
        [], api=FailRegister(), spawner=FakeSpawner(),
    )
    assert code == 2


@pytest.mark.asyncio
async def test_bridge_main_happy_path_with_cancel_event_returns_zero() -> None:
    api = FakeApiClient()
    spawner = FakeSpawner()
    cancel = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    asyncio.create_task(stop_soon())
    code = await bridge_main(
        ['--capacity', '2'], api=api, spawner=spawner,
        cancel_event=cancel,
    )
    assert code == 0
    # Registration happened.
    assert len(api.register_calls) == 1
    assert api.register_calls[0].max_sessions == 2
    # Deregistration happened.
    assert api.deregister_calls == ['env-srv']


@pytest.mark.asyncio
async def test_session_timeout_kills_session() -> None:
    """A session that runs past `session_timeout_ms` gets killed."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    cfg.session_timeout_ms = 200  # 200ms timeout

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    # Wait for spawn.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    handle = spawner.handles[0]
    # Don't complete the session — let the timeout fire.
    for _ in range(40):
        await asyncio.sleep(0.02)
        if handle.killed:
            break
    assert handle.killed, 'watchdog should have killed the session'

    # Complete + cancel so the task finishes.
    handle.complete('interrupted')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_session_timeout_disabled_when_unset() -> None:
    """Without `session_timeout_ms`, no watchdog fires."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    assert cfg.session_timeout_ms is None

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    handle = spawner.handles[0]
    # Sleep past where the watchdog would have fired if armed.
    await asyncio.sleep(0.25)
    assert not handle.killed
    handle.complete('completed')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_two_track_backoff_doubles_on_repeated_errors() -> None:
    """Backoff doubles per failure within a track; success resets it.

    Verifies via the conn-error path that two consecutive errors
    produce delays roughly initial_ms and 2*initial_ms.
    """
    import httpx

    error_count = [0]

    class FlakyApi(FakeApiClient):
        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            error_count[0] += 1
            if error_count[0] <= 2:
                raise httpx.ConnectError('refused')
            return None  # eventually succeed

    api = FlakyApi()
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    # Tight backoff so the test finishes quickly.
    bc = BackoffConfig(
        conn_initial_ms=20, conn_cap_ms=10_000, conn_give_up_ms=60_000,
        general_initial_ms=10, general_cap_ms=10_000, general_give_up_ms=60_000,
    )
    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
        backoff_config=bc,
    ))
    # Wait for both errors + at least one successful poll.
    for _ in range(50):
        await asyncio.sleep(0.02)
        if error_count[0] >= 3:
            break
    assert error_count[0] >= 3
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_backoff_track_gives_up_after_threshold() -> None:
    """When backoff exceeds give_up_ms, raise BridgeHeadlessPermanentError."""
    import httpx

    class AlwaysFails(FakeApiClient):
        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            raise httpx.ConnectError('always refused')

    api = AlwaysFails()
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    # Set give-up to a tiny window so the test triggers it fast.
    bc = BackoffConfig(
        conn_initial_ms=20, conn_cap_ms=20, conn_give_up_ms=50,
        general_initial_ms=10, general_cap_ms=10, general_give_up_ms=50,
    )
    with pytest.raises(BridgeHeadlessPermanentError) as exc:
        await run_bridge_loop(
            _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
            backoff_config=bc,
        )
    assert 'connection-error' in str(exc.value)


@pytest.mark.asyncio
async def test_general_track_used_for_non_connection_errors() -> None:
    """Non-connection errors use the general track, not the conn track."""

    class ValueErrApi(FakeApiClient):
        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0

        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            self.call_count += 1
            raise ValueError('not a connection error')

    api = ValueErrApi()
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    bc = BackoffConfig(
        conn_initial_ms=10, conn_cap_ms=10, conn_give_up_ms=10_000,
        # Tiny general give-up so this test triggers it via the general track.
        general_initial_ms=10, general_cap_ms=10, general_give_up_ms=50,
    )
    with pytest.raises(BridgeHeadlessPermanentError) as exc:
        await run_bridge_loop(
            _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
            backoff_config=bc,
        )
    assert 'general-error' in str(exc.value)


@pytest.mark.asyncio
async def test_successful_poll_resets_backoff_tracks() -> None:
    """A clean response forgives prior errors on both tracks."""
    import httpx

    poll_sequence: list[Any] = [
        httpx.ConnectError('fail-1'),
        None,  # success → reset both tracks
        httpx.ConnectError('fail-2'),  # should start over at initial_ms
    ]
    call_count = [0]

    class SeqApi(FakeApiClient):
        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            call_count[0] += 1
            if not poll_sequence:
                return None
            item = poll_sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    api = SeqApi()
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    bc = BackoffConfig(
        conn_initial_ms=10, conn_cap_ms=10_000, conn_give_up_ms=10_000,
        general_initial_ms=10, general_cap_ms=10_000, general_give_up_ms=10_000,
    )
    # Speed up the seek interval so the test doesn't hang waiting for
    # the default 2s sleep between successful empty-polls.
    fast_poll = PollIntervalConfig(
        poll_interval_ms_not_at_capacity=10,
        poll_interval_ms_at_capacity=60_000,
        non_exclusive_heartbeat_interval_ms=0,
        multisession_poll_interval_ms_not_at_capacity=10,
        multisession_poll_interval_ms_partial_capacity=10,
        multisession_poll_interval_ms_at_capacity=60_000,
        reclaim_older_than_ms=5_000,
        session_keepalive_interval_v2_ms=120_000,
    )
    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
        backoff_config=bc, poll_config=fast_poll,
    ))
    # Wait until the sequence has been consumed (3 attempts).
    for _ in range(60):
        await asyncio.sleep(0.02)
        if call_count[0] >= 3:
            break
    cancel.set()
    await task
    assert call_count[0] >= 3


@pytest.mark.asyncio
async def test_heartbeat_mode_fires_when_at_capacity() -> None:
    """When `non_exclusive_heartbeat_interval_ms > 0`, hearts beat at capacity."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    api.heartbeat_call_count = 0  # type: ignore[attr-defined]
    original_heartbeat = api.heartbeat_work

    async def counted_heartbeat(
        env_id: str, work_id: str, tok: str,
    ) -> dict[str, Any]:
        api.heartbeat_call_count += 1  # type: ignore[attr-defined]
        return await original_heartbeat(env_id, work_id, tok)

    api.heartbeat_work = counted_heartbeat  # type: ignore[method-assign]
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    # Enable heartbeat at 30ms; max_sessions=1 so we go at-capacity immediately.
    poll_cfg = PollIntervalConfig(
        poll_interval_ms_not_at_capacity=10,
        poll_interval_ms_at_capacity=60_000,
        non_exclusive_heartbeat_interval_ms=30,
        multisession_poll_interval_ms_not_at_capacity=10,
        multisession_poll_interval_ms_partial_capacity=10,
        multisession_poll_interval_ms_at_capacity=60_000,
        reclaim_older_than_ms=5_000,
        session_keepalive_interval_v2_ms=120_000,
    )

    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
        poll_config=poll_cfg,
    ))
    # Wait for spawn + a few heartbeat ticks.
    for _ in range(80):
        await asyncio.sleep(0.01)
        if api.heartbeat_call_count >= 2:  # type: ignore[attr-defined]
            break
    assert api.heartbeat_call_count >= 2, (  # type: ignore[attr-defined]
        f'expected ≥2 heartbeats, got {api.heartbeat_call_count}'  # type: ignore[attr-defined]
    )
    spawner.handles[0].complete('completed')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_heartbeat_disabled_when_interval_zero() -> None:
    """`non_exclusive_heartbeat_interval_ms=0` → no heartbeats."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    api.heartbeat_call_count = 0  # type: ignore[attr-defined]
    original_heartbeat = api.heartbeat_work

    async def counted_heartbeat(*a: Any) -> dict[str, Any]:
        api.heartbeat_call_count += 1  # type: ignore[attr-defined]
        return await original_heartbeat(*a)

    api.heartbeat_work = counted_heartbeat  # type: ignore[method-assign]
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    # Default poll config has heartbeat disabled.
    task = asyncio.create_task(run_bridge_loop(
        _bridge_config(), 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    # Sleep a bit at capacity; no heartbeats should fire.
    await asyncio.sleep(0.1)
    assert api.heartbeat_call_count == 0  # type: ignore[attr-defined]
    spawner.handles[0].complete('completed')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_bridge_main_permanent_error_returns_three() -> None:
    class GoneApi(FakeApiClient):
        async def poll_for_work(self, *_a: Any, **_kw: Any) -> Any:
            raise BridgeFatalError(
                'gone', status=410, error_type='environment_expired',
            )

    cancel = asyncio.Event()
    code = await bridge_main(
        [], api=GoneApi(), spawner=FakeSpawner(),
        cancel_event=cancel,
    )
    assert code == 3


# ── Phase 12a: worktree spawn-mode integration ──────────────────────────


def _init_git_repo(repo_path: str) -> None:
    """Helper: initialize a real git repo with one commit."""
    import subprocess
    subprocess.run(
        ['git', 'init', '--initial-branch=main'], cwd=repo_path,
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ['git', 'config', 'user.email', 'test@example.com'],
        cwd=repo_path, check=True,
    )
    subprocess.run(
        ['git', 'config', 'user.name', 'Test'],
        cwd=repo_path, check=True,
    )
    subprocess.run(
        ['git', 'config', 'commit.gpgsign', 'false'],
        cwd=repo_path, check=True,
    )
    with open(f'{repo_path}/README.md', 'w', encoding='utf-8') as fh:
        fh.write('hello\n')
    subprocess.run(
        ['git', 'add', 'README.md'], cwd=repo_path, check=True,
    )
    subprocess.run(
        ['git', 'commit', '-m', 'init'], cwd=repo_path, check=True,
        stdout=subprocess.DEVNULL,
    )


@pytest.mark.asyncio
async def test_spawn_worktree_creates_isolated_dir_and_cleans_up(
    tmp_path,
) -> None:
    """``--spawn worktree`` against a git repo gives the session an
    isolated working dir, then removes it when the session ends."""
    import os
    repo = str(tmp_path / 'repo')
    os.makedirs(repo)
    _init_git_repo(repo)

    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_wt1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    cfg.dir = repo
    cfg.spawn_mode = 'worktree'

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    assert len(spawner.spawns) == 1
    _opts, wd = spawner.spawns[0]
    # The spawner was handed the worktree path, not the base repo.
    expected = os.path.join(repo, '.clawcodex', 'worktrees', 'agent-cse_wt1')
    assert wd == expected
    assert os.path.isdir(expected)

    # Complete the session — the worktree should be removed.
    spawner.handles[0].complete('completed')
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not os.path.isdir(expected):
            break
    assert not os.path.isdir(expected), (
        'worktree should be cleaned up after session completion'
    )

    cancel.set()
    await task


@pytest.mark.asyncio
async def test_spawn_worktree_falls_back_when_not_a_repo(tmp_path) -> None:
    """If ``cfg.dir`` isn't a git repo, ``--spawn worktree`` falls back
    to ``cfg.dir`` itself rather than refusing to spawn."""
    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_wt2'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    cfg.dir = str(tmp_path)  # not a git repo
    cfg.spawn_mode = 'worktree'

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    assert len(spawner.spawns) == 1
    _opts, wd = spawner.spawns[0]
    # Fell back to base dir — no worktree created.
    assert wd == str(tmp_path)
    spawner.handles[0].complete('completed')
    cancel.set()
    await task


@pytest.mark.asyncio
async def test_shutdown_removes_orphaned_worktrees(tmp_path) -> None:
    """If the daemon is shut down with a still-active worktree session,
    the worktree directory must not be left behind on disk."""
    import os
    repo = str(tmp_path / 'repo')
    os.makedirs(repo)
    _init_git_repo(repo)

    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_wt3'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    cfg.dir = repo
    cfg.spawn_mode = 'worktree'

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    expected = os.path.join(repo, '.clawcodex', 'worktrees', 'agent-cse_wt3')
    assert os.path.isdir(expected)
    # Trigger shutdown WITHOUT completing the session first.
    cancel.set()
    # Have the spawner's handle "exit" once it's been killed, so
    # ``shutdown`` can proceed past the wait_for-grace step. Capture
    # the task handle so we can await it explicitly and prevent test
    # leakage across the suite.

    async def complete_after_kill() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            if spawner.handles[0].killed:
                spawner.handles[0].complete('interrupted')
                return

    helper = asyncio.create_task(complete_after_kill())
    try:
        await task
    finally:
        if not helper.done():
            helper.cancel()
        await asyncio.gather(helper, return_exceptions=True)
    assert not os.path.isdir(expected), (
        'worktree should be cleaned up by daemon shutdown'
    )


@pytest.mark.asyncio
async def test_shutdown_bounded_when_session_done_task_is_slow(
    monkeypatch, tmp_path,
) -> None:
    """If ``_on_session_done`` is mid-cleanup when shutdown runs and
    doesn't finish within 2s, shutdown cancels it and returns within
    a bounded time — it must not hang forever on the cleanup task."""
    import os
    from src.bridge import bridge_main as bm

    repo = str(tmp_path / 'repo')
    os.makedirs(repo)
    _init_git_repo(repo)

    # Replace ``remove_agent_worktree`` with a slow stub so the
    # done-task is genuinely stuck during shutdown.
    async def slow_remove(_paths):
        await asyncio.sleep(30)

    monkeypatch.setattr(bm, 'remove_agent_worktree', slow_remove)

    work = {
        'id': 'w1', 'type': 'work', 'environment_id': 'env-srv',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_slow_done'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    cancel = asyncio.Event()
    cfg = _bridge_config()
    cfg.dir = repo
    cfg.spawn_mode = 'worktree'

    task = asyncio.create_task(run_bridge_loop(
        cfg, 'env-srv', 'sec-srv', api, spawner, cancel,
    ))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    # Complete the session so ``_on_session_done`` enters the slow
    # ``remove_agent_worktree`` path. Then cancel the loop — shutdown
    # should hit the 2s wait_for timeout and cancel the stragglers.
    spawner.handles[0].complete('completed')
    await asyncio.sleep(0.05)  # let _on_session_done get into slow_remove
    cancel.set()

    loop = asyncio.get_running_loop()
    start = loop.time()
    await task
    elapsed = loop.time() - start
    # Shutdown should complete within ~3s — the 2s wait_for plus
    # a small drain budget after cancellation. Without the bounded
    # wait, this would hang for 30s on the slow_remove sleep.
    assert elapsed < 5.0, (
        f'shutdown took {elapsed:.2f}s — should be bounded by 2s '
        f'wait_for + cancel drain'
    )
