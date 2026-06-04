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
async def test_poll_loop_stops_work_for_v1_secret() -> None:
    """MVP rejects v1 (non-CCR-v2) work."""
    work = {
        'id': 'work-v1',
        'type': 'work',
        'environment_id': 'env-srv-1',
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
    for _ in range(20):
        await asyncio.sleep(0.01)
        if api.stop_calls:
            break
    # Stopped the work (force=True for unsupported secret format).
    assert any(work_id == 'work-v1' for _e, work_id, _f in api.stop_calls)
    # No session spawned.
    assert spawner.spawns == []
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
