"""Tests for ``src.bridge.repl_bridge`` (Phase 6 MVP slice).

Strategy:
- Inject a fake ``BridgeApiClient`` + fake ``SessionSpawner`` so we
  don't need real HTTP or subprocesses.
- Cover: init register/create happy path, init failure paths,
  perpetual-mode NotImplementedError, poll loop processes one session,
  teardown cleans up.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from src.bridge.repl_bridge import (
    BridgeCoreParams,
    ReplBridgeHandle,
    init_bridge_core,
)
from src.bridge.types import SessionDoneStatus


# ── Test doubles ──────────────────────────────────────────────────────────


class FakeApiClient:
    """In-memory ``BridgeApiClient``. Tests script behavior."""

    def __init__(
        self,
        *,
        register_result: dict[str, str] | None = None,
        register_raises: Exception | None = None,
        poll_results: list[Any] | None = None,
        heartbeat_result: dict[str, Any] | None = None,
    ) -> None:
        self.register_result = register_result or {
            'environment_id': 'env-srv-1',
            'environment_secret': 'sec-srv',
        }
        self.register_raises = register_raises
        self.poll_results = poll_results or []  # consumed in order; None = no work
        self.heartbeat_result = heartbeat_result or {
            'lease_extended': True, 'state': 'running',
        }

        # Call logs
        self.register_calls: list[Any] = []
        self.poll_calls: list[Any] = []
        self.ack_calls: list[tuple[str, str, str]] = []
        self.stop_calls: list[tuple[str, str, bool]] = []
        self.deregister_calls: list[str] = []
        self.archive_calls: list[str] = []
        self.reconnect_calls: list[tuple[str, str]] = []
        self.heartbeat_calls: list[tuple[str, str, str]] = []
        self.event_calls: list[tuple[str, dict[str, Any], str]] = []

    async def register_bridge_environment(self, config: Any) -> dict[str, str]:
        self.register_calls.append(config)
        if self.register_raises is not None:
            raise self.register_raises
        return self.register_result

    async def poll_for_work(self, env_id: str, secret: str, *_a: Any, **_kw: Any) -> Any:
        self.poll_calls.append((env_id, secret))
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
        self.archive_calls.append(sid)

    async def reconnect_session(self, env_id: str, sid: str) -> None:
        self.reconnect_calls.append((env_id, sid))

    async def heartbeat_work(
        self, env_id: str, work_id: str, tok: str
    ) -> dict[str, Any]:
        self.heartbeat_calls.append((env_id, work_id, tok))
        return self.heartbeat_result

    async def send_permission_response_event(
        self, sid: str, event: dict[str, Any], tok: str
    ) -> None:
        self.event_calls.append((sid, event, tok))


class FakeSessionHandle:
    """In-memory SessionHandle for spawn tests."""

    def __init__(self, session_id: str, access_token: str) -> None:
        self._session_id = session_id
        self._access_token = access_token
        self._stdin: list[str] = []
        self._kill_called = False
        self._force_kill_called = False
        self._done_future: asyncio.Future[SessionDoneStatus] = (
            asyncio.get_event_loop().create_future()
        )

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
        return await self._done_future

    def kill(self) -> None:
        self._kill_called = True

    def force_kill(self) -> None:
        self._force_kill_called = True

    def write_stdin(self, data: str) -> None:
        self._stdin.append(data)

    def update_access_token(self, token: str) -> None:
        self._access_token = token

    # Test hook
    def complete(self, status: SessionDoneStatus = 'completed') -> None:
        if not self._done_future.done():
            self._done_future.set_result(status)


class FakeSpawner:
    """In-memory ``SessionSpawner``."""

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


def _encode_work_secret(use_ccr_v2: bool = True) -> str:
    payload = {
        'version': 1,
        'session_ingress_token': 'sess-jwt-abc',
        'api_base_url': 'https://api.example.com',
        'sources': [],
        'auth': [],
        'use_code_sessions': use_ccr_v2,
    }
    raw = json.dumps(payload).encode('utf-8')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _make_params(
    create_session_result: str | None = 'cse_test',
    create_session_raises: Exception | None = None,
    archive_raises: Exception | None = None,
    perpetual: bool = False,
) -> BridgeCoreParams:
    state_log: list[Any] = []

    async def create_session(opts: dict[str, Any]) -> str | None:
        if create_session_raises is not None:
            raise create_session_raises
        return create_session_result

    async def archive_session(sid: str) -> None:
        if archive_raises is not None:
            raise archive_raises

    params = BridgeCoreParams(
        dir='/tmp/test',
        machine_name='test-host',
        branch='main',
        git_repo_url=None,
        title='Test',
        base_url='https://api.example.com',
        session_ingress_url='https://api.example.com',
        worker_type='claude_code',
        get_access_token=lambda: 'tok-oauth',
        create_session=create_session,
        archive_session=archive_session,
        on_state_change=lambda *a: state_log.append(a),
        perpetual=perpetual,
    )
    # Smuggle state log onto params for tests.
    params._state_log = state_log  # type: ignore[attr-defined]
    return params


# ── Init / pre-flight ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_perpetual_mode_writes_pointer_on_init(tmp_path) -> None:
    """Phase 12c: ``perpetual=True`` now succeeds (no more
    NotImplementedError) and writes the pointer file after init."""
    from src.bridge.bridge_pointer import read_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)  # write pointer here, not /tmp/test
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    pointer = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert pointer is not None
    assert pointer.environment_id == 'env-srv-1'
    assert pointer.session_id == 'cse_test'
    assert pointer.bridge_id == params.bridge_id
    await handle.teardown()
    # Clean teardown → pointer removed.
    after = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert after is None


@pytest.mark.asyncio
async def test_init_happy_path_returns_handle() -> None:
    params = _make_params()
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )

    assert handle is not None
    assert isinstance(handle, ReplBridgeHandle)
    assert handle.bridge_session_id == 'cse_test'
    assert handle.environment_id == 'env-srv-1'
    assert handle.session_ingress_url == 'https://api.example.com'
    assert len(api.register_calls) == 1
    # state log includes ('ready',)
    assert ('ready',) in params._state_log  # type: ignore[attr-defined]
    await handle.teardown()


@pytest.mark.asyncio
async def test_init_returns_none_when_register_fails() -> None:
    from src.bridge.exceptions import BridgeFatalError

    params = _make_params()
    api = FakeApiClient(
        register_raises=BridgeFatalError('boom', status=500),
    )
    out = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert out is None
    # State log records the failure.
    assert any('failed' in str(s) for s in params._state_log)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_init_deregisters_when_session_create_fails() -> None:
    params = _make_params(create_session_result=None)
    api = FakeApiClient()
    out = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert out is None
    # Deregister was called as cleanup.
    assert api.deregister_calls == ['env-srv-1']


@pytest.mark.asyncio
async def test_init_handles_create_session_exception() -> None:
    params = _make_params(
        create_session_raises=RuntimeError('boom in create'),
    )
    api = FakeApiClient()
    out = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert out is None
    assert api.deregister_calls == ['env-srv-1']


# ── Poll loop processes work ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_loop_spawns_session_for_work_item() -> None:
    work = {
        'id': 'work-1',
        'type': 'work',
        'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    # Let the poll loop pick up the work.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break

    # Spawned exactly once with the work's session ID + token.
    assert len(spawner.spawns) == 1
    opts, working_dir = spawner.spawns[0]
    assert opts['session_id'] == 'cse_w1'
    assert opts['access_token'] == 'sess-jwt-abc'
    assert opts['use_ccr_v2'] is True
    assert working_dir == '/tmp/test'
    # Work item was ack'd.
    assert any(work_id == 'work-1' for _e, work_id, _t in api.ack_calls)
    # State 'connected' fired.
    assert ('connected',) in params._state_log  # type: ignore[attr-defined]
    await handle.teardown()


@pytest.mark.asyncio
async def test_poll_loop_dispatches_v1_work_with_session_ingress_url() -> None:
    """Phase 14c: v1 work items spawn with the session-ingress WS URL
    derived from ``params.session_ingress_url`` (NOT from
    ``secret.api_base_url`` — see TS ``bridgeMain.ts:905-907``).
    The work secret's ``api_base_url`` is set to a DIFFERENT host so a
    regression to ``secret.api_base_url`` would be caught here."""
    # Work secret carries a "remote proxy/tunnel" api_base_url that
    # is intentionally distinct from the bridge's configured
    # session_ingress_url. v1 must use the bridge's ingress URL,
    # not the proxy URL.
    import base64
    import json
    secret_payload = {
        'version': 1,
        'session_ingress_token': 'sess-jwt-abc',
        'api_base_url': 'https://remote-proxy.example.com',
        'sources': [],
        'auth': [],
        'use_code_sessions': False,
    }
    raw = json.dumps(secret_payload).encode('utf-8')
    encoded_secret = base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')

    work = {
        'id': 'work-v1',
        'type': 'work',
        'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v1'},
        'secret': encoded_secret,
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    # Override session_ingress_url so it differs from the secret's
    # api_base_url — proves v1 picks the right source.
    params.session_ingress_url = 'https://bridge-local.example.com'
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break
    assert len(spawner.spawns) == 1
    opts, _working_dir = spawner.spawns[0]
    # Session-ingress WS URL derived from the BRIDGE's session_ingress_url,
    # not from the secret's api_base_url. Without this distinction the
    # v1 dispatch would (incorrectly) use 'remote-proxy.example.com'.
    assert opts['sdk_url'] == (
        'wss://bridge-local.example.com/v1/session_ingress/ws/cse_v1'
    )
    assert opts['use_ccr_v2'] is False
    assert opts['access_token'] == 'sess-jwt-abc'
    # Work was ack'd, NOT stopped.
    assert any(work_id == 'work-v1' for _e, work_id, _t in api.ack_calls)
    assert not any(
        work_id == 'work-v1' for _e, work_id, _f in api.stop_calls
    )
    await handle.teardown()


@pytest.mark.asyncio
async def test_poll_loop_dispatches_v2_work_with_ccr_v2_url() -> None:
    """Phase 14c regression: v2 work uses ``secret.api_base_url``
    (the server-controlled CCR endpoint), distinct from the bridge's
    ``session_ingress_url``. Verifies the v2 path didn't accidentally
    pick up the v1 URL source."""
    work = {
        'id': 'work-v2',
        'type': 'work',
        'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': _encode_work_secret(use_ccr_v2=True),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    # Distinct host from secret.api_base_url so we'd notice if the
    # builder accidentally pulled from session_ingress_url instead.
    params.session_ingress_url = 'https://bridge-local.example.com'
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.spawns:
            break
    assert len(spawner.spawns) == 1
    opts, _working_dir = spawner.spawns[0]
    # CCR v2 URL — uses secret.api_base_url ('api.example.com'), NOT
    # the bridge's session_ingress_url ('bridge-local.example.com').
    assert opts['sdk_url'] == (
        'https://api.example.com/v1/code/sessions/cse_v2'
    )
    assert opts['use_ccr_v2'] is True
    await handle.teardown()


@pytest.mark.asyncio
async def test_poll_loop_handles_healthcheck_work() -> None:
    """Healthcheck work is ack'd without spawning."""
    work = {
        'id': 'hc-1',
        'type': 'work',
        'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'healthcheck', 'id': 'hc-1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if api.ack_calls:
            break
    assert spawner.spawns == []  # no session spawned for healthcheck
    # Healthcheck still uses the env secret for the ack (we don't decode
    # the work secret on healthcheck — short-circuit). The MVP passes
    # env_secret as the ack token for healthcheck.
    await handle.teardown()


@pytest.mark.asyncio
async def test_poll_loop_gives_up_after_recreation_exhausted() -> None:
    """Phase 11b: 404 (env lost) triggers env recreation; after
    `max_env_recreation_attempts` failed attempts, gives up with 'failed'.
    """
    from src.bridge.exceptions import BridgeFatalError
    from src.bridge.poll_config_defaults import PollIntervalConfig

    api = FakeApiClient()
    spawner = FakeSpawner()
    # Set max_env_recreation_attempts=1 so the test bounds quickly.
    # Also speed up the poll interval so the retry happens promptly.
    params = _make_params()
    params.max_env_recreation_attempts = 1
    fast_cfg = PollIntervalConfig(
        poll_interval_ms_not_at_capacity=20,
        poll_interval_ms_at_capacity=60_000,
        non_exclusive_heartbeat_interval_ms=0,
        multisession_poll_interval_ms_not_at_capacity=20,
        multisession_poll_interval_ms_partial_capacity=20,
        multisession_poll_interval_ms_at_capacity=60_000,
        reclaim_older_than_ms=5_000,
        session_keepalive_interval_v2_ms=120_000,
    )
    params.get_poll_interval_config = lambda: fast_cfg
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None

    # Make poll always 404, AND make recreation register fail so the
    # attempt counter increments without succeeding.
    async def poll_404(*_a: Any, **_kw: Any) -> Any:
        raise BridgeFatalError('not found', status=404)

    async def register_fail(_c: Any) -> dict[str, str]:
        raise BridgeFatalError('register failed', status=500)

    api.poll_for_work = poll_404  # type: ignore[method-assign]
    api.register_bridge_environment = register_fail  # type: ignore[method-assign]

    # Let the loop attempt recreation then exhaust the budget.
    for _ in range(100):
        await asyncio.sleep(0.02)
        # 'reconnecting' followed by 'failed' indicates exhausted recreation.
        if any('failed' in str(s) for s in params._state_log):  # type: ignore[attr-defined]
            break
    state_strs = [str(s) for s in params._state_log]  # type: ignore[attr-defined]
    # Should have fired both reconnecting + failed.
    assert any('reconnecting' in s for s in state_strs)
    assert any('failed' in s for s in state_strs)
    await handle.teardown()


@pytest.mark.asyncio
async def test_env_recreation_succeeds_and_resets_attempts() -> None:
    """A successful recreation resumes polling and resets the attempt counter."""
    from src.bridge.exceptions import BridgeFatalError

    api = FakeApiClient()
    spawner = FakeSpawner()
    params = _make_params()
    params.max_env_recreation_attempts = 2
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None

    call_count = [0]

    async def poll_404_then_ok(*_a: Any, **_kw: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            raise BridgeFatalError('not found', status=404)
        return None  # empty poll after recreation

    api.poll_for_work = poll_404_then_ok  # type: ignore[method-assign]
    # Recreation register + create_session should succeed (default behavior).

    # Wait until we've seen the 404 + a successful poll.
    for _ in range(60):
        await asyncio.sleep(0.02)
        if call_count[0] >= 2:
            break
    assert call_count[0] >= 2
    state_strs = [str(s) for s in params._state_log]  # type: ignore[attr-defined]
    # 'reconnecting' fired during recreation, then 'ready' on success.
    assert any('reconnecting' in s for s in state_strs)
    # After successful recreation the env recreation attempt counter
    # is reset (visible via the second 'ready' event).
    assert sum(1 for s in state_strs if s == "('ready',)") >= 2
    await handle.teardown()


@pytest.mark.asyncio
async def test_dropped_batch_count_increments_on_write_failure() -> None:
    """Failed stdin writes increment dropped_batch_count for observability."""
    from src.types.messages import UserMessage

    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break

    # Make the session's write_stdin raise.
    def boom(_data: str) -> None:
        raise BrokenPipeError('child closed')

    spawner.handles[0].write_stdin = boom  # type: ignore[method-assign]

    # Reach into the internal state object via the handle's callable.
    # The dropped_batch_count lives on the _BridgeState; we access it
    # via the handle's send_result method's closure-like reference.
    handle.write_messages([UserMessage(content='hi', uuid='u-1')])
    handle.write_messages([UserMessage(content='hi-2', uuid='u-2')])

    # Drop-count is observable via the state object that owns the
    # handle's write_messages callable. We expose it by sampling the
    # underlying object via the test-only attribute.
    # (The state object's address isn't returned in the handle's public
    # surface, so we use the callable's __self__ to reach it.)
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.dropped_batch_count == 2

    spawner.handles[0].complete('completed')
    await handle.teardown()


# ── Phase 15: JWT refresh v1/v2 split ────────────────────────────────────


@pytest.mark.asyncio
async def test_v1_token_refresh_pushes_to_session_via_stdin() -> None:
    """v1 work item (use_code_sessions=False): on_refresh pushes the
    fresh OAuth/JWT to the child via session.update_access_token,
    and does NOT call api.reconnect_session."""
    work = {
        'id': 'work-v1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v1'},
        'secret': _encode_work_secret(use_ccr_v2=False),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    assert spawner.handles
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.active_use_ccr_v2 is False
    # Snapshot the initial reconnect_calls count (Strategy-1 reconnect
    # is unrelated; this guards against test ordering surprises).
    initial_reconnects = len(api.reconnect_calls)

    # Fire on_refresh directly via the scheduler's stored callback.
    state.active_token_refresh._on_refresh('cse_v1', 'fresh-oauth-token')
    # Let any scheduled tasks run.
    await asyncio.sleep(0.02)
    # v1 path: token pushed to child stdin.
    assert spawner.handles[0].access_token == 'fresh-oauth-token'
    # v1 must NOT call reconnect_session.
    assert len(api.reconnect_calls) == initial_reconnects

    spawner.handles[0].complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_v2_token_refresh_calls_reconnect_session() -> None:
    """v2 work item (use_code_sessions=True): on_refresh schedules
    api.reconnect_session(env_id, session_id), and does NOT push to
    the child's stdin (CCR worker endpoints validate the JWT's
    session_id claim — pushing OAuth would break them)."""
    work = {
        'id': 'work-v2', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': _encode_work_secret(use_ccr_v2=True),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    assert spawner.handles
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.active_use_ccr_v2 is True

    initial_reconnects = len(api.reconnect_calls)
    initial_child_token = spawner.handles[0].access_token

    state.active_token_refresh._on_refresh('cse_v2', 'fresh-oauth-token')
    # Let the scheduled reconnect task run.
    await asyncio.sleep(0.05)
    # v2 path: reconnect_session called with (env, session_id).
    assert len(api.reconnect_calls) == initial_reconnects + 1
    env_id, sid = api.reconnect_calls[-1]
    assert sid == 'cse_v2'
    # Child's access_token unchanged — v2 doesn't push via stdin.
    assert spawner.handles[0].access_token == initial_child_token

    spawner.handles[0].complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_v2_token_refresh_reconnect_failure_swallowed() -> None:
    """If api.reconnect_session raises during v2 refresh, no
    exception propagates and the bridge state is preserved (the
    scheduler's follow-up timer will retry naturally)."""
    work = {
        'id': 'work-v2', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': _encode_work_secret(use_ccr_v2=True),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    state = handle.write_messages.__self__  # type: ignore[attr-defined]

    # Make reconnect_session raise.
    async def reconnect_raise(_env: str, _sid: str) -> None:
        raise RuntimeError('server unavailable')
    api.reconnect_session = reconnect_raise  # type: ignore[method-assign]

    # Fire on_refresh — must not raise.
    state.active_token_refresh._on_refresh('cse_v2', 'fresh-oauth-token')
    await asyncio.sleep(0.05)
    # Bridge state is intact.
    assert state.active_session is not None
    assert state.active_use_ccr_v2 is True

    spawner.handles[0].complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_session_done_clears_v2_flag() -> None:
    """After a v2 session completes, ``active_use_ccr_v2`` is reset
    to False so the next spawn starts clean."""
    work = {
        'id': 'work-v2', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': _encode_work_secret(use_ccr_v2=True),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.active_use_ccr_v2 is True

    spawner.handles[0].complete('completed')
    # Wait for _await_session_done to run.
    for _ in range(30):
        await asyncio.sleep(0.01)
        if not state.active_use_ccr_v2:
            break
    assert state.active_use_ccr_v2 is False
    await handle.teardown()


@pytest.mark.asyncio
async def test_existing_handle_path_updates_token_for_redispatched_work(
) -> None:
    """Phase 15 CRITIC: when work arrives for an already-active session
    (e.g. server re-dispatched after a v2 JWT refresh), the bridge
    updates the existing handle's token + reschedules the refresh,
    and does NOT spawn a duplicate subprocess. Mirrors TS
    ``bridgeMain.ts:868-885``."""
    import base64
    import json
    import time

    payload = {'exp': int(time.time()) + 3600, 'session_id': 'cse_v2'}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode('utf-8'),
    ).rstrip(b'=').decode('ascii')
    real_jwt = f'header.{payload_b64}.signature'

    # First work item: triggers normal spawn.
    secret_v2_initial: dict[str, Any] = {
        'version': 1,
        'session_ingress_token': real_jwt,
        'api_base_url': 'https://api.example.com',
        'sources': [],
        'auth': [],
        'use_code_sessions': True,
    }
    raw_v2 = json.dumps(secret_v2_initial).encode('utf-8')
    encoded_v2 = base64.urlsafe_b64encode(raw_v2).rstrip(b'=').decode('ascii')
    initial_work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': encoded_v2,
        'created_at': '2026-05-26',
    }
    api = FakeApiClient(poll_results=[initial_work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(30):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    assert len(spawner.spawns) == 1
    existing_handle = spawner.handles[0]
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.active_session is existing_handle
    initial_work_id = state.active_work_id

    # Simulate a server re-dispatch: synthesize a second work item
    # with the SAME session_id but a different work_id (the server
    # would issue a new work_id on re-dispatch) + a fresh JWT.
    fresh_payload = {
        'exp': int(time.time()) + 7200, 'session_id': 'cse_v2',
    }
    fresh_b64 = base64.urlsafe_b64encode(
        json.dumps(fresh_payload).encode('utf-8'),
    ).rstrip(b'=').decode('ascii')
    fresh_jwt = f'header.{fresh_b64}.signature'
    secret_v2_redispatch = {**secret_v2_initial, 'session_ingress_token': fresh_jwt}
    raw_re = json.dumps(secret_v2_redispatch).encode('utf-8')
    encoded_re = base64.urlsafe_b64encode(raw_re).rstrip(b'=').decode('ascii')
    redispatch_work = {
        'id': 'work-redispatched', 'type': 'work',
        'environment_id': 'env-srv-1', 'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_v2'},
        'secret': encoded_re,
        'created_at': '2026-05-26',
    }
    # Invoke _process_work directly with the redispatch.
    await state._process_work(redispatch_work)
    # NO new spawn.
    assert len(spawner.spawns) == 1
    # Existing handle's token bumped.
    assert existing_handle.access_token == fresh_jwt
    # work_id rolled to the redispatch.
    assert state.active_work_id == 'work-redispatched'
    assert state.active_work_id != initial_work_id

    # Cleanup.
    existing_handle.complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_jwt_refresh_scheduler_armed_on_spawn() -> None:
    """When a session is spawned, the JWT refresh scheduler is created."""
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break

    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.active_token_refresh is not None
    spawner.handles[0].complete('completed')
    # After session done, the scheduler should be cancelled + cleared.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if state.active_token_refresh is None:
            break
    assert state.active_token_refresh is None
    await handle.teardown()


# ── Teardown ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teardown_archives_and_deregisters() -> None:
    api = FakeApiClient()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    await handle.teardown()
    assert api.deregister_calls == ['env-srv-1']
    # Initial session was archived via the injected callback (not via API).


@pytest.mark.asyncio
async def test_teardown_is_idempotent() -> None:
    api = FakeApiClient()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    await handle.teardown()
    await handle.teardown()
    # Only one deregister.
    assert len(api.deregister_calls) == 1


@pytest.mark.asyncio
async def test_teardown_kills_active_session() -> None:
    work = {
        'id': 'work-1',
        'type': 'work',
        'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    # Wait for spawn.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    fake_session = spawner.handles[0]

    # Teardown — should kill, wait briefly, then close.
    # Since the fake's wait_done blocks forever, teardown will hit the
    # 2s force_kill timeout. Speed it up by completing it manually shortly.
    async def auto_complete() -> None:
        await asyncio.sleep(0.05)
        fake_session.complete('interrupted')

    asyncio.create_task(auto_complete())
    await handle.teardown()

    assert fake_session._kill_called


@pytest.mark.asyncio
async def test_teardown_archives_via_injected_callback() -> None:
    """``params.archive_session`` is called on teardown."""
    archived: list[str] = []

    async def archive(sid: str) -> None:
        archived.append(sid)

    params = _make_params()
    params.archive_session = archive  # override
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    await handle.teardown()
    assert archived == ['cse_test']


@pytest.mark.asyncio
async def test_teardown_swallows_archive_exceptions() -> None:
    """Archive errors must not prevent deregister."""
    params = _make_params(archive_raises=RuntimeError('archive boom'))
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    await handle.teardown()
    # Deregister still happened.
    assert api.deregister_calls == ['env-srv-1']


# ── Write methods (MVP forwards to child stdin) ─────────────────────────


@pytest.mark.asyncio
async def test_write_messages_forwards_to_active_session() -> None:
    from src.types.messages import UserMessage

    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break

    handle.write_messages([UserMessage(content='hello', uuid='u-1')])
    sent = spawner.handles[0]._stdin
    assert len(sent) == 1
    parsed = json.loads(sent[0])
    assert parsed['type'] == 'user'
    assert parsed['uuid'] == 'u-1'
    assert parsed['message']['content'] == 'hello'
    # Clean teardown.
    spawner.handles[0].complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_write_messages_noop_when_no_active_session() -> None:
    """write_messages is a no-op until a session has been spawned."""
    from src.types.messages import UserMessage

    api = FakeApiClient()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # No session yet — write must not crash.
    handle.write_messages([UserMessage(content='hi', uuid='u-x')])
    await handle.teardown()


@pytest.mark.asyncio
async def test_send_control_request_forwards_when_active() -> None:
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(20):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    handle.send_control_request({'type': 'control_request', 'request_id': 'r1'})
    handle.send_cancel_request('r1')
    sent = spawner.handles[0]._stdin
    assert any('control_request' in s for s in sent)
    assert any('control_cancel_request' in s for s in sent)
    spawner.handles[0].complete('completed')
    await handle.teardown()


# ── Phase 12b: Strategy-1 in-place reconnect ─────────────────────────────
#
# These tests directly exercise ``_BridgeState._recreate_environment``
# rather than going through the poll-404 flow, because the MVP's poll
# loop intentionally suspends polling while a session is active (see
# the ``self.active_session is not None`` branch at the top of
# ``_poll_loop``). That's the right MVP behavior — a single-session
# bridge has nothing to ask the server about while busy — but it means
# the 404-detection path is exercised by other API calls in real life,
# not by the poll. Testing ``_recreate_environment`` directly lets us
# validate Strategy-1 ↔ Strategy-2 dispatch without inventing fake
# heartbeat/SSE-error injection.


@pytest.mark.asyncio
async def test_strategy_1_reconnect_preserves_session_when_server_accepts(
) -> None:
    """When ``reconnect_session`` succeeds AND the server resurrected
    the same env id, the daemon must NOT create a fresh session, NOT
    kill the active one, NOT archive the old one — just clear the
    stale work id, refresh the env_secret, and resume polling."""
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    params.max_env_recreation_attempts = 2
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(40):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    assert spawner.handles
    original_session = spawner.handles[0]
    original_session_id = original_session.session_id

    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    prior_env_id = state.environment_id
    pre_archive = list(api.archive_calls)
    pre_stop = list(api.stop_calls)

    # Server honors ``reuse_environment_id`` — returns the same env id
    # with a fresh secret. This is the Strategy-1 precondition.
    api.register_result = {
        'environment_id': prior_env_id,
        'environment_secret': 'sec-srv-fresh',
    }

    ok = await state._recreate_environment()

    assert ok is True
    # Strategy-1 invoked with the SAME env id and original session id.
    assert api.reconnect_calls == [(prior_env_id, original_session_id)]
    # Env id unchanged; secret swapped to the fresh value.
    assert state.environment_id == prior_env_id
    assert state.environment_secret == 'sec-srv-fresh'
    # Session NOT killed, NOT archived, NOT replaced.
    assert not original_session._kill_called
    assert api.archive_calls == pre_archive
    assert state.active_session is original_session
    assert state.active_session_id == original_session_id
    # Stale work id is stopped (best-effort) and cleared so the next
    # poll picks up a fresh work-secret bound to the new env-secret.
    new_stops = [s for s in api.stop_calls if s not in pre_stop]
    assert any(s[1] == 'work-1' for s in new_stops), (
        f'stale work-id should be stop-worked; new_stops={new_stops!r}'
    )
    assert state.active_work_id is None
    # ``reuse_environment_id`` hint was set and then cleared.
    assert state.bridge_config.reuse_environment_id is None

    original_session.complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_strategy_1_skipped_when_server_assigns_new_env_id(
) -> None:
    """If the server doesn't honor ``reuse_environment_id`` and hands
    back a different env_id, Strategy-1 must be SKIPPED (the prior
    session is bound to the dead env). Falls through to Strategy-2."""
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    params.max_env_recreation_attempts = 2

    archived: list[str] = []
    original_archive = params.archive_session
    async def recording_archive(sid: str) -> None:
        archived.append(sid)
        return await original_archive(sid)
    params.archive_session = recording_archive  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(40):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    original_session = spawner.handles[0]
    state = handle.write_messages.__self__  # type: ignore[attr-defined]

    # Server returns a DIFFERENT env id — Strategy-1 precondition
    # fails, fallback to Strategy-2.
    api.register_result = {
        'environment_id': 'env-srv-different',
        'environment_secret': 'sec-srv-different',
    }

    ok = await state._recreate_environment()

    assert ok is True
    # Strategy-1 was NOT attempted — env-id mismatch short-circuited.
    assert api.reconnect_calls == []
    # Strategy-2 ran instead.
    assert original_session._kill_called
    assert original_session.session_id in archived
    assert state.environment_id == 'env-srv-different'

    await handle.teardown()


@pytest.mark.asyncio
async def test_strategy_1_falls_back_to_strategy_2_on_reconnect_refuse(
) -> None:
    """When ``reconnect_session`` raises, the daemon must fall back to
    Strategy-2: kill the old session, archive it, create a fresh one."""
    from src.bridge.exceptions import BridgeFatalError

    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])

    # reconnect_session refuses with a session-expired error.
    async def reconnect_refuse(env_id: str, sid: str) -> None:
        api.reconnect_calls.append((env_id, sid))
        raise BridgeFatalError(
            'session not found', status=404, error_type='session_expired',
        )
    api.reconnect_session = reconnect_refuse  # type: ignore[method-assign]

    spawner = FakeSpawner()
    params = _make_params()
    params.max_env_recreation_attempts = 2

    # Wrap params.archive_session to record what was archived.
    archived: list[str] = []
    original_archive = params.archive_session
    async def recording_archive(sid: str) -> None:
        archived.append(sid)
        return await original_archive(sid)
    params.archive_session = recording_archive  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(40):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    original_session = spawner.handles[0]
    original_session_id = original_session.session_id
    state = handle.write_messages.__self__  # type: ignore[attr-defined]

    # Server honors reuse — Strategy-1 precondition satisfied, but
    # reconnect_session itself refuses → falls through to Strategy-2.
    api.register_result = {
        'environment_id': state.environment_id,  # same env id
        'environment_secret': 'sec-srv-fresh',
    }

    ok = await state._recreate_environment()

    # Strategy-1 was attempted first, then Strategy-2 took over.
    assert ok is True
    assert api.reconnect_calls == [(state.environment_id, original_session_id)]
    # Strategy-2 effects:
    assert original_session._kill_called, (
        'Strategy-2 killed the original session'
    )
    assert original_session_id in archived, (
        f'Strategy-2 should archive the active session id; '
        f'archived={archived!r}'
    )
    # The internal session-id pointer was updated to the new session
    # that create_session returned (default 'cse_test').
    assert state.initial_session_id == 'cse_test'
    assert state.active_session is None

    await handle.teardown()


@pytest.mark.asyncio
async def test_strategy_1_skipped_when_no_active_session() -> None:
    """If there's no active session at recreation time, Strategy-1 must
    NOT call ``reconnect_session`` — there's no session id to preserve."""
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-2',
        'environment_secret': 'sec-srv-2',
    }
    spawner = FakeSpawner()
    params = _make_params()
    params.max_env_recreation_attempts = 2
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    # No work has been dispatched, so no active session.
    assert state.active_session is None
    assert state.active_session_id is None

    ok = await state._recreate_environment()

    assert ok is True
    # Strategy-1 skipped → no reconnect call.
    assert api.reconnect_calls == []
    # Strategy-2 path ran: re-registered env + created new session.
    assert state.environment_id == 'env-srv-2'
    assert state.initial_session_id == 'cse_test'

    await handle.teardown()


@pytest.mark.asyncio
async def test_strategy_1_re_register_failure_returns_false() -> None:
    """If the env re-registration itself fails, ``_recreate_environment``
    must return False without attempting reconnect or fresh-session
    create — the caller's retry loop will back off and try again."""
    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_w1'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    params = _make_params()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(40):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    original_session = spawner.handles[0]

    # Make register fail.
    # Use a realistic transport-class failure (mirrors what
    # ``_with_oauth_retry`` raises for 5xx).
    from src.bridge.exceptions import BridgeFatalError
    api.register_raises = BridgeFatalError(
        'temporary backend outage', status=503,
    )
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    pre_reconnect = list(api.reconnect_calls)
    pre_archive = list(api.archive_calls)

    ok = await state._recreate_environment()

    assert ok is False
    # Neither Strategy-1 nor Strategy-2 actions occurred after re-
    # register failed.
    assert api.reconnect_calls == pre_reconnect
    assert api.archive_calls == pre_archive
    assert not original_session._kill_called

    original_session.complete('completed')
    await handle.teardown()


# ── Phase 12c: perpetual mode + crash-recovery pointer ───────────────────


@pytest.mark.asyncio
async def test_perpetual_resumes_session_when_pointer_and_env_reuse_succeed(
    tmp_path,
) -> None:
    """When ``perpetual=True`` and the pointer exists, init must reuse
    both the env id (via ``reuse_environment_id``) and the session id
    (skipping ``create_session`` entirely)."""
    from src.bridge.bridge_pointer import write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    # Seed a pointer pointing at env-srv-7 / session cse_resumed.
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-7',
        session_id='cse_resumed',
        machine_name=params.machine_name,
    )
    # Configure the fake API to "resurrect" the env id when reuse is
    # requested.
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-7',
        'environment_secret': 'sec-srv-7',
    }
    # Track create_session calls so we can assert it was NOT called.
    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # ``reuse_environment_id`` was hinted in the register call.
    assert api.register_calls, 'expected register call'
    sent_config = api.register_calls[-1]
    assert sent_config.reuse_environment_id == 'env-srv-7'
    # Phase 13: reuse only after the server confirms the session is
    # still alive via reconnect_session.
    assert api.reconnect_calls == [('env-srv-7', 'cse_resumed')]
    # Session reused, NOT created.
    assert handle.bridge_session_id == 'cse_resumed'
    assert create_calls == []
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_falls_back_when_server_assigns_new_env_id(
    tmp_path,
) -> None:
    """If the server ignores ``reuse_environment_id`` and returns a
    different env id, init must drop the pointer's session id (it's
    bound to the dead env) and create a fresh session."""
    from src.bridge.bridge_pointer import write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-OLD',
        session_id='cse_dead',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    # Server doesn't honor reuse — returns a different env id.
    api.register_result = {
        'environment_id': 'env-srv-FRESH',
        'environment_secret': 'sec-fresh',
    }
    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # Hint was sent, but the server gave back a different id.
    assert api.register_calls[-1].reuse_environment_id == 'env-srv-OLD'
    # Fresh session was created (not reused).
    assert create_calls, 'create_session should have been called'
    assert handle.bridge_session_id == 'cse_test'  # default fake result
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_starts_pointer_mtime_refresh_task(tmp_path) -> None:
    """Phase 17: in perpetual mode, the periodic pointer-mtime refresh
    task is started at init."""
    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    handle = await init_bridge_core(
        params, api_client=FakeApiClient(), spawner=FakeSpawner(),
    )
    assert handle is not None
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.pointer_mtime_task is not None
    assert not state.pointer_mtime_task.done()
    await handle.teardown()
    # Task is cancelled + cleared on teardown.
    assert state.pointer_mtime_task is None


@pytest.mark.asyncio
async def test_non_perpetual_does_not_start_pointer_mtime_task(
    tmp_path,
) -> None:
    """Phase 17: non-perpetual mode skips the mtime refresh task —
    the pointer is cleared on teardown anyway, no maintenance needed."""
    params = _make_params(perpetual=False)
    params.dir = str(tmp_path)
    handle = await init_bridge_core(
        params, api_client=FakeApiClient(), spawner=FakeSpawner(),
    )
    assert handle is not None
    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    assert state.pointer_mtime_task is None
    await handle.teardown()


@pytest.mark.asyncio
async def test_pointer_mtime_task_fires_and_advances_updated_at_ms(
    tmp_path, monkeypatch,
) -> None:
    """Phase 17: when the refresh interval elapses, the pointer's
    ``updated_at_ms`` advances (proves the task fires + writes)."""
    from src.bridge import repl_bridge as rb
    from src.bridge.bridge_pointer import read_pointer

    # Short interval so the test finishes in ~0.1s instead of 1h.
    monkeypatch.setattr(rb, 'POINTER_MTIME_REFRESH_INTERVAL_S', 0.05)

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    handle = await init_bridge_core(
        params, api_client=FakeApiClient(), spawner=FakeSpawner(),
    )
    assert handle is not None

    # Snapshot the initial pointer state.
    initial = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert initial is not None
    initial_updated_at_ms = initial.updated_at_ms
    initial_created_at_ms = initial.created_at_ms

    # Wait for at least one refresh tick to fire.
    await asyncio.sleep(0.1)

    refreshed = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert refreshed is not None
    # updated_at_ms advanced (mtime refresh happened).
    assert refreshed.updated_at_ms > initial_updated_at_ms
    # created_at_ms preserved (the daemon's install time doesn't reset).
    assert refreshed.created_at_ms == initial_created_at_ms
    # bridge_id + env_id unchanged.
    assert refreshed.bridge_id == initial.bridge_id
    assert refreshed.environment_id == initial.environment_id

    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_env_mismatch_clears_stale_pointer_eagerly(
    tmp_path,
) -> None:
    """Phase 16: on env-mismatch, the stale pointer is cleared
    eagerly so that if ``create_session`` subsequently fails, a
    next-start doesn't re-hint the dead env again. Mirrors TS
    ``replBridge.ts:429-431``."""
    from src.bridge.bridge_pointer import read_pointer, write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-OLD',
        session_id='cse_dead',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    # Server returns DIFFERENT env id (mismatch).
    api.register_result = {
        'environment_id': 'env-srv-FRESH',
        'environment_secret': 'sec-fresh',
    }
    # Make create_session fail so we can observe the post-mismatch
    # pointer state — without the eager clear, a stale pointer with
    # env-srv-OLD would survive on disk.
    async def failing_create(_opts: dict[str, Any]) -> str | None:
        return None
    params.create_session = failing_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    # init_bridge_core returns None when create_session fails.
    assert handle is None
    # Pointer was cleared on env-mismatch BEFORE create_session ran;
    # the subsequent create_session failure didn't leave a stale
    # env-srv-OLD pointer on disk. The pointer file is gone.
    p = read_pointer(params.dir, machine_name=params.machine_name)
    assert p is None


@pytest.mark.asyncio
async def test_perpetual_ignores_stale_pointer_from_different_dir(
    tmp_path,
) -> None:
    """A pointer file written for a different working directory must
    be rejected; init starts fresh as if no pointer existed."""
    from src.bridge.bridge_pointer import write_pointer
    import json
    import os

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    # Write a pointer that claims a DIFFERENT dir.
    pointer_dir = os.path.join(params.dir, '.claude')
    os.makedirs(pointer_dir, exist_ok=True)
    with open(
        os.path.join(pointer_dir, 'bridge-pointer.json'),
        'w', encoding='utf-8',
    ) as fh:
        json.dump({
            'schema_version': 1,
            'bridge_id': 'br-old',
            'environment_id': 'env-srv-OLD',
            'session_id': 'cse_old',
            'machine_name': params.machine_name,
            'dir': '/some/other/working/dir',
            'created_at_ms': 1000,
            'updated_at_ms': 2000,
        }, fh)
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # Stale pointer should not have hinted reuse.
    assert api.register_calls[-1].reuse_environment_id is None
    # Fresh session created.
    assert handle.bridge_session_id == 'cse_test'
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_clears_pointer_on_clean_teardown(tmp_path) -> None:
    """``teardown()`` must remove the pointer so a subsequent start
    doesn't try to resume an env we just deregistered."""
    from src.bridge.bridge_pointer import read_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    handle = await init_bridge_core(
        params, api_client=FakeApiClient(), spawner=FakeSpawner(),
    )
    assert handle is not None
    # Pointer exists during the run.
    assert read_pointer(
        params.dir, machine_name=params.machine_name,
    ) is not None
    await handle.teardown()
    # Pointer removed after teardown.
    assert read_pointer(
        params.dir, machine_name=params.machine_name,
    ) is None


@pytest.mark.asyncio
async def test_non_perpetual_does_not_write_pointer(tmp_path) -> None:
    """``perpetual=False`` (the default) must NOT write a pointer —
    avoiding pointer pollution in non-recovery use cases."""
    from src.bridge.bridge_pointer import read_pointer

    params = _make_params(perpetual=False)
    params.dir = str(tmp_path)
    handle = await init_bridge_core(
        params, api_client=FakeApiClient(), spawner=FakeSpawner(),
    )
    assert handle is not None
    assert read_pointer(
        params.dir, machine_name=params.machine_name,
    ) is None
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_pointer_updates_when_work_spawns_session(
    tmp_path,
) -> None:
    """When ``_process_work`` spawns a session for a server-provided
    session_id (which may differ from the bootstrap id), the pointer
    must be rewritten so a mid-session crash recovers correctly."""
    from src.bridge.bridge_pointer import read_pointer

    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_from_work'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    # Initial pointer has the bootstrap session id.
    initial = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert initial is not None
    assert initial.session_id == 'cse_test'

    # Wait for the work item to be processed.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    assert spawner.handles

    # Pointer should now reflect the work's session_id, not the
    # bootstrap one.
    after_spawn = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert after_spawn is not None
    assert after_spawn.session_id == 'cse_from_work'
    # ``created_at_ms`` is preserved across the update.
    assert after_spawn.created_at_ms == initial.created_at_ms

    spawner.handles[0].complete('completed')
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_pointer_clears_session_when_session_completes(
    tmp_path,
) -> None:
    """After ``_await_session_done`` runs, the pointer must drop its
    ``session_id`` (set it to None) — a crash before the next poll
    would otherwise try to resurrect an archived session."""
    from src.bridge.bridge_pointer import read_pointer

    work = {
        'id': 'work-1', 'type': 'work', 'environment_id': 'env-srv-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'cse_short_lived'},
        'secret': _encode_work_secret(),
        'created_at': '2026-05-24',
    }
    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    api = FakeApiClient(poll_results=[work])
    spawner = FakeSpawner()
    handle = await init_bridge_core(
        params, api_client=api, spawner=spawner,
    )
    assert handle is not None
    for _ in range(50):
        await asyncio.sleep(0.01)
        if spawner.handles:
            break
    # Pointer has the active session id at this point.
    mid = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert mid is not None
    assert mid.session_id == 'cse_short_lived'

    # Session ends.
    spawner.handles[0].complete('completed')
    # Wait for _await_session_done to run.
    for _ in range(50):
        await asyncio.sleep(0.01)
        p = read_pointer(
            params.dir, machine_name=params.machine_name,
        )
        if p is not None and p.session_id is None:
            break

    final = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert final is not None
    assert final.session_id is None, (
        'pointer should null session_id after session completes'
    )
    # Env id is unchanged so the next start can still resume the env.
    assert final.environment_id == mid.environment_id
    assert final.bridge_id == mid.bridge_id

    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_pointer_updates_after_strategy_2_recreation(
    tmp_path,
) -> None:
    """After Strategy-2 swaps env+session, the pointer must reflect
    the NEW identities — a crash mid-recreation should recover into
    the new env, not the dead one."""
    from src.bridge.bridge_pointer import read_pointer

    # Cycle the fake create_session through distinct ids so the
    # "after recreate" session id is observably different from
    # the initial one.
    session_ids = iter(['cse_initial', 'cse_after_strat2'])
    async def cycling_create(_opts: dict[str, Any]) -> str | None:
        return next(session_ids)
    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    params.create_session = cycling_create  # type: ignore[assignment]
    api = FakeApiClient()
    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    initial = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert initial is not None
    initial_env = initial.environment_id
    initial_session = initial.session_id
    assert initial_session == 'cse_initial'

    state = handle.write_messages.__self__  # type: ignore[attr-defined]
    # Force Strategy-2: server hands back a DIFFERENT env id.
    api.register_result = {
        'environment_id': 'env-srv-strategy2',
        'environment_secret': 'sec-s2',
    }
    ok = await state._recreate_environment()
    assert ok is True

    after = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert after is not None
    assert after.environment_id == 'env-srv-strategy2'
    assert after.environment_id != initial_env
    # Strategy-2 minted a new session id from the cycling fixture.
    assert after.session_id == 'cse_after_strat2'
    assert after.session_id != initial_session
    # Created_at_ms preserved across the update.
    assert after.created_at_ms == initial.created_at_ms
    # Updated_at_ms bumped (or at least not regressed).
    assert after.updated_at_ms >= initial.updated_at_ms

    await handle.teardown()


# ── Phase 13: validate pointer's session_id via reconnect ─────────────────


@pytest.mark.asyncio
async def test_perpetual_validates_session_via_reconnect_before_reuse(
    tmp_path,
) -> None:
    """Phase 13: when reuse is eligible (env resurrected), init must
    call ``reconnect_session`` to validate the session is still alive
    before skipping ``create_session``."""
    from src.bridge.bridge_pointer import read_pointer, write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-A',
        session_id='cse_alive',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-A',
        'environment_secret': 'sec-a',
    }
    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # Exactly one reconnect attempt (cse_ tag has no session_ variant).
    assert api.reconnect_calls == [('env-srv-A', 'cse_alive')]
    # Validation passed → session reused, no create_session.
    assert create_calls == []
    assert handle.bridge_session_id == 'cse_alive'
    # Pointer still present (validation passed, not cleared).
    p = read_pointer(params.dir, machine_name=params.machine_name)
    assert p is not None
    assert p.session_id == 'cse_alive'
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_falls_back_when_reconnect_session_refused(
    tmp_path,
) -> None:
    """Phase 13: when the server refuses ``reconnect_session`` for every
    candidate id, init must clear the pointer, mint a fresh session via
    ``create_session``, and write the fresh id into the pointer."""
    from src.bridge.bridge_pointer import read_pointer, write_pointer
    from src.bridge.exceptions import BridgeFatalError

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-B',
        session_id='cse_dead',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-B',
        'environment_secret': 'sec-b',
    }
    # Every reconnect attempt is refused (e.g., session was reaped).
    async def reconnect_refuse(env_id: str, sid: str) -> None:
        api.reconnect_calls.append((env_id, sid))
        raise BridgeFatalError('Session not found', status=404)
    api.reconnect_session = reconnect_refuse  # type: ignore[method-assign]

    # Capture the original created_at_ms to verify continuity.
    original = read_pointer(
        params.dir, machine_name=params.machine_name,
    )
    assert original is not None
    original_created_at_ms = original.created_at_ms

    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # All candidates tried (cse_dead is already cse_, so just one).
    assert api.reconnect_calls == [('env-srv-B', 'cse_dead')]
    # Validation failed → fall through to create_session.
    assert create_calls, 'create_session must have been called'
    # Bridge holds the freshly-minted session id, not the dead one.
    assert handle.bridge_session_id == 'cse_test'
    # Pointer was rewritten with the fresh session id (init re-writes
    # the pointer after create_session lands a new session).
    p = read_pointer(params.dir, machine_name=params.machine_name)
    assert p is not None
    assert p.session_id == 'cse_test'
    assert p.environment_id == 'env-srv-B'
    # created_at_ms must persist across the clear+rewrite — it's the
    # daemon install time, not a per-session timestamp.
    assert p.created_at_ms == original_created_at_ms
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_reconnect_tries_session_then_cse_flavor(
    tmp_path,
) -> None:
    """Phase 13: when the pointer's id is ``session_*`` tagged, init must
    try the ``session_*`` form first and fall back to the ``cse_*``
    form on failure (the server's v2-compat-gate may flip between
    pointer-write and pointer-read). The canonical session id used
    by the bridge afterward is the pointer's original tag — TS uses
    ``prior.sessionId`` regardless of which candidate validated."""
    from src.bridge.bridge_pointer import write_pointer
    from src.bridge.exceptions import BridgeFatalError

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    # Pointer was written when the gate was OFF (session_* tag).
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-C',
        session_id='session_xyz',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-C',
        'environment_secret': 'sec-c',
    }
    # First attempt (session_xyz) fails — gate has since flipped ON;
    # second attempt (cse_xyz) succeeds.
    call_count = {'n': 0}
    async def reconnect_flaky(env_id: str, sid: str) -> None:
        api.reconnect_calls.append((env_id, sid))
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise BridgeFatalError('Session not found', status=404)
        # Second call succeeds.
    api.reconnect_session = reconnect_flaky  # type: ignore[method-assign]

    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # Both candidates tried, in the right order.
    assert api.reconnect_calls == [
        ('env-srv-C', 'session_xyz'),
        ('env-srv-C', 'cse_xyz'),
    ]
    # Validation succeeded on the second attempt → no create_session.
    assert create_calls == []
    # Canonical id is the pointer's original tag, matching TS behavior.
    assert handle.bridge_session_id == 'session_xyz'
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_reconnect_stops_at_first_success(
    tmp_path,
) -> None:
    """Phase 13: when the first candidate (``session_*``) succeeds, the
    second (``cse_*``) must not be attempted — the ``break`` after a
    success guards against wasted server round-trips. Inverse of
    ``test_perpetual_reconnect_tries_session_then_cse_flavor``."""
    from src.bridge.bridge_pointer import write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    # Pointer with session_* tag → two candidates would be generated.
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-D',
        session_id='session_yyy',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-D',
        'environment_secret': 'sec-d',
    }
    # First (and only) reconnect attempt succeeds — default
    # FakeApiClient.reconnect_session does nothing on success.

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # ONLY the session_* form was tried; cse_yyy was never attempted.
    assert api.reconnect_calls == [('env-srv-D', 'session_yyy')]
    assert handle.bridge_session_id == 'session_yyy'
    await handle.teardown()


@pytest.mark.asyncio
async def test_perpetual_skips_reconnect_validation_when_env_mismatch(
    tmp_path,
) -> None:
    """Phase 13: when the server hands back a different env id than the
    one the pointer requested, ``reuse_session_id`` is already nulled
    by the env-mismatch branch and the reconnect validation block
    must NOT fire (the session is bound to the dead env anyway)."""
    from src.bridge.bridge_pointer import write_pointer

    params = _make_params(perpetual=True)
    params.dir = str(tmp_path)
    write_pointer(
        params.dir,
        bridge_id=params.bridge_id,
        environment_id='env-srv-OLD',
        session_id='cse_orphan',
        machine_name=params.machine_name,
    )
    api = FakeApiClient()
    api.register_result = {
        'environment_id': 'env-srv-FRESH',
        'environment_secret': 'sec-fresh',
    }
    create_calls: list[Any] = []
    original_create = params.create_session
    async def recording_create(opts: dict[str, Any]) -> str | None:
        create_calls.append(opts)
        return await original_create(opts)
    params.create_session = recording_create  # type: ignore[assignment]

    handle = await init_bridge_core(
        params, api_client=api, spawner=FakeSpawner(),
    )
    assert handle is not None
    # Validation skipped entirely — env mismatch already invalidated
    # the pointer's session id.
    assert api.reconnect_calls == []
    # Fresh session was created.
    assert create_calls, 'create_session must have been called'
    assert handle.bridge_session_id == 'cse_test'
    await handle.teardown()
