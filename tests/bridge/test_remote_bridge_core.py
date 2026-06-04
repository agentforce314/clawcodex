"""Tests for ``src.bridge.remote_bridge_core`` — the Phase 5 MVP.

Uses a fake v2 transport (no SSE / no CCRClient) injected via
``transport_factory`` so we can exercise the full orchestrator code path
including refresh, rebuild, teardown, and 401 recovery without going to
the network. The HTTP layer (code-session create + /bridge fetch +
archive) is mocked via ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.bridge.env_less_bridge_config import (
    DEFAULT_ENV_LESS_BRIDGE_CONFIG,
    EnvLessBridgeConfig,
)
from src.bridge.remote_bridge_core import (
    EnvLessBridgeParams,
    RemoteBridgeHandle,
    init_env_less_bridge_core,
)
from src.bridge.repl_bridge_transport import V2TransportOptions
from src.types.messages import UserMessage


# ── Test doubles ──────────────────────────────────────────────────────────


class FakeTransport:
    """Minimal in-memory ``ReplBridgeTransport`` for orchestrator tests.

    Tracks every method call so tests can assert what the orchestrator
    requested. Models the production ``CCRClient.write_event`` semantics
    where writes issued before ``_initialized`` is True are dropped
    (incrementing ``dropped_batch_count``) — this catches BLOCKING
    regressions where the orchestrator doesn't queue properly during
    handshake/rebuild.

    Two modes:

    * ``defer_connect=False`` (default) — ``connect()`` synchronously
      flips ``initialized`` and fires ``set_on_connect``. Simulates a
      transport that handshakes instantly.
    * ``defer_connect=True`` — ``connect()`` schedules but does NOT
      complete handshake until ``trigger_connect_complete()`` is called.
      Use this to reproduce the pre-init write window.
    """

    def __init__(self, *, defer_connect: bool = False) -> None:
        self.writes: list[dict[str, Any]] = []
        self.batches: list[list[dict[str, Any]]] = []
        self.states: list[Any] = []
        self.closed: bool = False
        self.connect_called: bool = False
        self.initialized: bool = False
        self.dropped: int = 0
        self.last_seq_num: int = 0
        self._defer_connect = defer_connect
        self._on_connect: Any = None
        self._on_data: Any = None
        self._on_close: Any = None

    async def write(self, message: dict[str, Any]) -> None:
        if self.closed or not self.initialized:
            self.dropped += 1
            return
        self.writes.append(message)

    async def write_batch(self, messages: list[dict[str, Any]]) -> None:
        if self.closed or not self.initialized:
            self.dropped += 1
            return
        self.batches.append(list(messages))

    def close(self) -> None:
        self.closed = True

    def is_connected_status(self) -> bool:
        return self.initialized and not self.closed

    def get_state_label(self) -> str:
        return 'fake'

    def set_on_data(self, cb: Any) -> None:
        self._on_data = cb

    def set_on_close(self, cb: Any) -> None:
        self._on_close = cb

    def set_on_connect(self, cb: Any) -> None:
        self._on_connect = cb

    def connect(self) -> None:
        self.connect_called = True
        if not self._defer_connect:
            self.trigger_connect_complete()

    def get_last_sequence_num(self) -> int:
        return self.last_seq_num

    @property
    def dropped_batch_count(self) -> int:
        return self.dropped

    def report_state(self, state: Any) -> None:
        self.states.append(state)

    def report_metadata(self, metadata: Any) -> None:  # noqa: ARG002
        pass

    def report_delivery(self, event_id: str, status: str) -> None:  # noqa: ARG002
        pass

    async def flush(self) -> None:
        pass

    # Test-only hooks
    def trigger_close(self, code: int | None = None) -> None:
        if self._on_close is not None:
            self._on_close(code)

    def trigger_data(self, payload: str) -> None:
        if self._on_data is not None:
            self._on_data(payload)

    def trigger_connect_complete(self) -> None:
        """Flip ``initialized`` and fire the on_connect callback."""
        self.initialized = True
        if self._on_connect is not None:
            self._on_connect()


def _b64url(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload).encode('utf-8')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _short_config() -> EnvLessBridgeConfig:
    """Default config with shorter timings so retries are fast."""
    return EnvLessBridgeConfig(
        init_retry_max_attempts=1,
        init_retry_base_delay_ms=100,
        init_retry_max_delay_ms=500,
        http_timeout_ms=2000,
        connect_timeout_ms=5000,
        teardown_archive_timeout_ms=1000,
    )


def _make_http_handler(
    *,
    create_session_status: int = 200,
    create_session_id: str = 'cse_abc123',
    bridge_status: int = 200,
    bridge_payload: dict[str, Any] | None = None,
    archive_status: int = 200,
) -> Any:
    """Build an httpx MockTransport handler covering the three endpoints.

    Returns ``(handler, calls)`` where ``calls`` is a list of
    ``(path, headers, body)`` tuples for assertions.
    """
    calls: list[tuple[str, dict[str, str], Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        calls.append((path, dict(req.headers), body))
        if path == '/v1/code/sessions':
            if create_session_status >= 400:
                return httpx.Response(create_session_status, json={
                    'error': {'type': 'create_failed'}
                })
            return httpx.Response(200, json={
                'session': {'id': create_session_id},
            })
        if path.startswith('/v1/code/sessions/') and path.endswith('/bridge'):
            if bridge_status >= 400:
                return httpx.Response(bridge_status, json={
                    'error': {'type': 'bridge_failed'}
                })
            payload = bridge_payload or {
                'worker_jwt': 'jwt-1',
                'api_base_url': 'https://api.example.com',
                'expires_in': 3600,
                'worker_epoch': 5,
            }
            return httpx.Response(200, json=payload)
        if path.startswith('/v1/sessions/') and path.endswith('/archive'):
            return httpx.Response(archive_status, json={})
        return httpx.Response(404, json={'error': 'no route'})

    return handler, calls


def _make_params(
    transport: FakeTransport,
    **overrides: Any,
) -> tuple[EnvLessBridgeParams, dict[str, Any], list[Any]]:
    """Build the standard EnvLessBridgeParams + capture buckets for callbacks."""
    state_changes: list[Any] = []
    inbound_log: list[Any] = []

    def fake_transport_factory(opts: V2TransportOptions) -> Any:
        async def _make() -> FakeTransport:
            # Pin the most-recent opts for assertions if needed.
            transport.last_opts = opts  # type: ignore[attr-defined]
            return transport
        return _make()

    base = dict(
        base_url='https://api.example.com',
        org_uuid='org-1',
        title='Test session',
        get_access_token=lambda: 'tok-oauth',
        initial_history_cap=200,
        on_state_change=lambda *a: state_changes.append(a),
        on_inbound_message=lambda msg: inbound_log.append(msg),
    )
    base.update(overrides)
    return (
        EnvLessBridgeParams(**base),
        {
            'state_changes': state_changes,
            'inbound_log': inbound_log,
            'transport_factory': fake_transport_factory,
        },
        [],
    )


# ── Init: happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_returns_handle_on_happy_path() -> None:
    transport = FakeTransport()
    handler, calls = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params,
            http_client=client,
            config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )

        assert handle is not None
        assert isinstance(handle, RemoteBridgeHandle)
        assert handle.bridge_session_id == 'cse_abc123'
        assert handle.environment_id == ''  # always empty for env-less
        assert handle.session_ingress_url == 'https://api.example.com'
        assert transport.connect_called
        # State transitions: 'ready' (post-init) → 'connected' (post-onConnect, no flush)
        assert ('ready',) in ctx['state_changes']
        assert ('connected',) in ctx['state_changes']
        # HTTP calls: /sessions then /bridge
        assert calls[0][0] == '/v1/code/sessions'
        assert calls[1][0].startswith('/v1/code/sessions/') and calls[1][0].endswith('/bridge')
        await handle.teardown()


@pytest.mark.asyncio
async def test_init_returns_none_when_no_oauth_token() -> None:
    transport = FakeTransport()
    handler, _calls = _make_http_handler()
    params, ctx, _ = _make_params(transport, get_access_token=lambda: None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params,
            http_client=client,
            config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )

    assert handle is None
    assert not transport.connect_called


@pytest.mark.asyncio
async def test_init_returns_none_when_session_create_fails() -> None:
    transport = FakeTransport()
    handler, _calls = _make_http_handler(create_session_status=500)
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params,
            http_client=client,
            config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )

    assert handle is None
    # State: failed callback fires
    assert any('failed' in str(s) for s in ctx['state_changes'])


@pytest.mark.asyncio
async def test_init_returns_none_when_bridge_credentials_fail() -> None:
    transport = FakeTransport()
    handler, calls = _make_http_handler(bridge_status=500)
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params,
            http_client=client,
            config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )

    assert handle is None
    # Pre-flight archive attempted (best-effort)
    archive_calls = [c for c in calls if '/archive' in c[0]]
    assert len(archive_calls) == 1


@pytest.mark.asyncio
async def test_init_archives_on_transport_factory_failure() -> None:
    transport = FakeTransport()
    handler, calls = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    def failing_factory(opts: V2TransportOptions) -> Any:
        async def _raise() -> FakeTransport:
            raise RuntimeError('boom')
        return _raise()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params,
            http_client=client,
            config=_short_config(),
            transport_factory=failing_factory,
        )

    assert handle is None
    archive_calls = [c for c in calls if '/archive' in c[0]]
    assert len(archive_calls) == 1
    # Failed state fired
    assert any('failed' in str(s) for s in ctx['state_changes'])


# ── Write paths ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_messages_sends_via_transport_batch() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        handle.write_messages([UserMessage(content='hi', uuid='u-1')])
        # Give the fire-and-forget create_task a chance to run.
        await asyncio.sleep(0)
        assert len(transport.batches) == 1
        assert transport.batches[0][0]['uuid'] == 'u-1'
        assert transport.batches[0][0]['session_id'] == 'cse_abc123'
        # A user message triggers report_state('running')
        assert any(s.get('state') == 'running' for s in transport.states)
        await handle.teardown()


@pytest.mark.asyncio
async def test_write_messages_dedups_against_initial_messages() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    initial = [UserMessage(content='first', uuid='u-init')]
    params, ctx, _ = _make_params(transport, initial_messages=initial)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        await asyncio.sleep(0)
        # Initial flush via the connect callback wrote 1 batch.
        initial_batch_count = len(transport.batches)

        # Now try to re-write the SAME uuid — should be filtered.
        handle.write_messages([UserMessage(content='first', uuid='u-init')])
        await asyncio.sleep(0)
        assert len(transport.batches) == initial_batch_count
        await handle.teardown()


@pytest.mark.asyncio
async def test_write_messages_dedups_against_recent_posted() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        msg = UserMessage(content='hi', uuid='u-dup')
        handle.write_messages([msg])
        await asyncio.sleep(0)
        handle.write_messages([msg])  # same uuid, second write
        await asyncio.sleep(0)
        # Only one batch — the second write was deduped.
        assert len(transport.batches) == 1
        await handle.teardown()


@pytest.mark.asyncio
async def test_write_messages_queues_during_flush() -> None:
    """Writes that arrive while flush_gate is active are queued.

    Post BLOCKING-1 fix, the gate starts unconditionally on init. When
    the synchronous fake transport's onConnect fires during connect(),
    it schedules the async flush_history task — between scheduling and
    running, the gate is still active. A write that lands here is
    correctly enqueued and drained later.
    """
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    initial = [UserMessage(content='history', uuid='u-init')]
    params, ctx, _ = _make_params(transport, initial_messages=initial)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        # The history flush task has been scheduled but not run; gate is active.
        handle.write_messages([UserMessage(content='live', uuid='u-live')])
        # Now let the scheduled tasks run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Eventually a batch with the live message appears (after drain).
        all_uuids = [
            m['uuid'] for batch in transport.batches for m in batch
        ]
        assert 'u-live' in all_uuids
        await handle.teardown()


@pytest.mark.asyncio
async def test_write_messages_drops_unsendable_types() -> None:
    """Non-eligible message types (e.g. progress) are filtered out."""
    from src.types.messages import ProgressMessage

    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        handle.write_messages([
            ProgressMessage(content='', uuid='p-1', toolUseID='t-1'),
        ])
        await asyncio.sleep(0)
        # No batch written.
        assert transport.batches == []
        await handle.teardown()


# ── send_* control methods ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_control_request_writes_event() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        handle.send_control_request({
            'type': 'control_request',
            'request_id': 'req-1',
            'request': {'subtype': 'can_use_tool'},
        })
        await asyncio.sleep(0)
        assert len(transport.writes) >= 1
        # Latest write carries the request_id and session_id.
        sent = next(w for w in transport.writes if w.get('request_id') == 'req-1')
        assert sent['session_id'] == 'cse_abc123'
        # can_use_tool triggers reportState('requires_action')
        assert any(s.get('state') == 'requires_action' for s in transport.states)
        await handle.teardown()


@pytest.mark.asyncio
async def test_send_result_writes_result_message() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        before = len(transport.writes)
        handle.send_result()
        await asyncio.sleep(0)
        assert len(transport.writes) == before + 1
        assert transport.writes[-1]['type'] == 'result'
        # Reports state idle.
        assert any(s.get('state') == 'idle' for s in transport.states)
        await handle.teardown()


@pytest.mark.asyncio
async def test_send_cancel_request_writes_event() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        handle.send_cancel_request('req-cancel')
        await asyncio.sleep(0)
        cancel_writes = [w for w in transport.writes if w.get('type') == 'control_cancel_request']
        assert len(cancel_writes) == 1
        assert cancel_writes[0]['request_id'] == 'req-cancel'
        await handle.teardown()


# ── Teardown ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teardown_writes_result_and_archives() -> None:
    transport = FakeTransport()
    handler, calls = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        await handle.teardown()

    # Archive HTTP call fired.
    archive_calls = [c for c in calls if '/archive' in c[0]]
    assert len(archive_calls) == 1
    # Compat retag: cse_abc123 → session_abc123 in the archive URL.
    assert 'session_abc123' in archive_calls[0][0]
    # Result message written before close.
    result_writes = [w for w in transport.writes if w.get('type') == 'result']
    assert len(result_writes) == 1
    # Transport closed.
    assert transport.closed


@pytest.mark.asyncio
async def test_teardown_is_idempotent() -> None:
    transport = FakeTransport()
    handler, calls = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        await handle.teardown()
        await handle.teardown()  # must not raise or double-archive

    archive_calls = [c for c in calls if '/archive' in c[0]]
    assert len(archive_calls) == 1


@pytest.mark.asyncio
async def test_teardown_retries_archive_on_401() -> None:
    transport = FakeTransport()
    calls: list[tuple[str, dict[str, str], Any]] = []
    archive_call_count = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        calls.append((path, dict(req.headers), body))
        if path == '/v1/code/sessions':
            return httpx.Response(200, json={'session': {'id': 'cse_x'}})
        if path.endswith('/bridge'):
            return httpx.Response(200, json={
                'worker_jwt': 'jwt-1', 'api_base_url': 'https://api.example.com',
                'expires_in': 3600, 'worker_epoch': 5,
            })
        if path.endswith('/archive'):
            archive_call_count[0] += 1
            if archive_call_count[0] == 1:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={})
        return httpx.Response(404)

    refresh_attempts = [0]

    async def on_auth_401(_stale: str) -> bool:
        refresh_attempts[0] += 1
        return True

    params, ctx, _ = _make_params(
        transport,
        on_auth_401=on_auth_401,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        await handle.teardown()

    # Archive called twice (initial 401 + retry); refresh fired once.
    assert archive_call_count[0] == 2
    assert refresh_attempts[0] == 1


# ── Inbound message routing ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inbound_user_message_fires_callback() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        # Simulate an inbound user message via the data callback.
        payload = json.dumps({
            'type': 'user',
            'message': {'role': 'user', 'content': 'remote prompt'},
            'uuid': 'inbound-1',
        })
        transport.trigger_data(payload)
        await asyncio.sleep(0)
        # Callback fired with the parsed dict.
        assert len(ctx['inbound_log']) == 1
        assert ctx['inbound_log'][0]['uuid'] == 'inbound-1'
        await handle.teardown()


@pytest.mark.asyncio
async def test_inbound_user_echo_is_filtered() -> None:
    """Server echoes of our own posted messages are deduped."""
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        # Post a message, then receive it back.
        handle.write_messages([UserMessage(content='hi', uuid='u-echo')])
        await asyncio.sleep(0)
        # Server echoes it back.
        payload = json.dumps({
            'type': 'user',
            'message': {'role': 'user', 'content': 'hi'},
            'uuid': 'u-echo',
        })
        transport.trigger_data(payload)
        await asyncio.sleep(0)
        # Inbound callback NOT fired for the echo.
        assert len(ctx['inbound_log']) == 0
        await handle.teardown()


# ── 401 recovery ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_close_401_triggers_recovery() -> None:
    """A 401 SSE close triggers OAuth refresh + transport rebuild."""
    transport = FakeTransport()
    calls: list[tuple[str, dict[str, str], Any]] = []
    bridge_call_count = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        calls.append((path, dict(req.headers), None))
        if path == '/v1/code/sessions':
            return httpx.Response(200, json={'session': {'id': 'cse_x'}})
        if path.endswith('/bridge'):
            bridge_call_count[0] += 1
            return httpx.Response(200, json={
                'worker_jwt': f'jwt-{bridge_call_count[0]}',
                'api_base_url': 'https://api.example.com',
                'expires_in': 3600,
                'worker_epoch': bridge_call_count[0],
            })
        if path.endswith('/archive'):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    # The second factory call returns a fresh fake transport so the
    # rebuild path uses a separate object — lets us verify the swap.
    transports = [transport, FakeTransport()]
    factory_call_count = [0]

    def factory(opts: V2TransportOptions) -> Any:
        async def _make() -> FakeTransport:
            t = transports[factory_call_count[0]]
            factory_call_count[0] += 1
            return t
        return _make()

    async def on_auth_401(_stale: str) -> bool:
        return True

    params = EnvLessBridgeParams(
        base_url='https://api.example.com',
        org_uuid='org-1',
        title='t',
        get_access_token=lambda: 'oauth-tok',
        initial_history_cap=200,
        on_auth_401=on_auth_401,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=factory,
        )
        assert handle is not None

        # Trigger the 401 SSE close.
        transport.trigger_close(401)
        # Wait for the spawned recovery task to complete.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if factory_call_count[0] == 2:
                break

    # Rebuild happened: factory called twice, /bridge called twice.
    assert factory_call_count[0] == 2
    assert bridge_call_count[0] == 2


@pytest.mark.asyncio
async def test_on_close_non_401_marks_failed() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        transport.trigger_close(4090)  # epoch mismatch — terminal
        await asyncio.sleep(0)
        assert any('failed' in str(s) for s in ctx['state_changes'])
        await handle.teardown()


# ── on_user_message title-derivation callback ────────────────────────────


@pytest.mark.asyncio
async def test_on_user_message_called_until_returns_true() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    user_msg_calls: list[tuple[str, str]] = []

    def on_user_message(text: str, sid: str) -> bool:
        user_msg_calls.append((text, sid))
        return len(user_msg_calls) >= 2  # done after 2nd call

    params, ctx, _ = _make_params(
        transport,
        on_user_message=on_user_message,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        handle.write_messages([UserMessage(content='msg-1', uuid='u-1')])
        handle.write_messages([UserMessage(content='msg-2', uuid='u-2')])
        handle.write_messages([UserMessage(content='msg-3', uuid='u-3')])
        await asyncio.sleep(0)
        # Called for u-1 (returned False) and u-2 (returned True); not for u-3.
        assert len(user_msg_calls) == 2
        assert user_msg_calls[0][0] == 'msg-1'
        assert user_msg_calls[1][0] == 'msg-2'
        await handle.teardown()


# ── Refresh scheduler integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_schedules_token_refresh() -> None:
    """The refresh scheduler is armed with the credentials' expires_in."""
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    # Patch TokenRefreshScheduler.schedule_from_expires_in to verify it's called.
    with patch(
        'src.bridge.remote_bridge_core.TokenRefreshScheduler.schedule_from_expires_in'
    ) as mock_sched:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            handle = await init_env_less_bridge_core(
                params, http_client=client, config=_short_config(),
                transport_factory=ctx['transport_factory'],
            )
            assert handle is not None
            mock_sched.assert_called_once_with('cse_abc123', 3600)
            await handle.teardown()


# ── State callback ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_during_handshake_does_not_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test per CRITIC BLOCKING-1.

    Production ``CCRClient.write_event`` silently drops while
    ``_initialized is False``. The orchestrator MUST start the flush_gate
    on init (not only when initial_messages is set) so any write between
    ``init_env_less_bridge_core`` returning and the first ``_on_connect``
    is queued, not dropped.
    """
    transport = FakeTransport(defer_connect=True)
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        # CCR is NOT yet initialized — a naive write would drop.
        assert not transport.initialized

        handle.write_messages([UserMessage(content='pre-init', uuid='u-pre')])
        await asyncio.sleep(0)
        # No batch yet (write was queued in the flush_gate).
        assert transport.batches == []
        assert transport.dropped == 0

        # Now complete the handshake — the on_connect callback drains
        # the gate, which finally sends the queued batch.
        transport.trigger_connect_complete()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Batch landed; nothing dropped.
        all_uuids = [m['uuid'] for batch in transport.batches for m in batch]
        assert 'u-pre' in all_uuids
        assert transport.dropped == 0
        await handle.teardown()


@pytest.mark.asyncio
async def test_write_during_rebuild_does_not_drop() -> None:
    """Regression test per CRITIC BLOCKING-2.

    A JWT-refresh rebuild creates a new transport. Writes that race the
    new transport's CCR.initialize() must NOT be drained early — the
    orchestrator should leave the gate active until the new transport's
    ``_on_connect`` fires.
    """
    initial_transport = FakeTransport()
    rebuild_transport = FakeTransport(defer_connect=True)
    handler, _ = _make_http_handler()

    transports = [initial_transport, rebuild_transport]
    factory_calls = [0]

    def factory(opts: V2TransportOptions) -> Any:
        async def _make() -> FakeTransport:
            t = transports[factory_calls[0]]
            factory_calls[0] += 1
            return t
        return _make()

    async def on_auth_401(_stale: str) -> bool:
        return True

    params = EnvLessBridgeParams(
        base_url='https://api.example.com', org_uuid='org-1', title='t',
        get_access_token=lambda: 'oauth-tok',
        initial_history_cap=200, on_auth_401=on_auth_401,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=factory,
        )
        assert handle is not None
        # Trigger 401 SSE close → kicks off recovery → rebuild.
        initial_transport.trigger_close(401)
        # Let the rebuild task progress through its awaits to where
        # the new transport.connect() has been called but CCR isn't
        # yet initialized.
        for _ in range(10):
            await asyncio.sleep(0.01)
            if rebuild_transport.connect_called:
                break

        assert rebuild_transport.connect_called
        assert not rebuild_transport.initialized

        # Write during the rebuild window — naively this would drop.
        handle.write_messages([
            UserMessage(content='during-rebuild', uuid='u-rb')
        ])
        await asyncio.sleep(0)

        # No batch yet (write queued in gate).
        assert rebuild_transport.batches == []
        assert rebuild_transport.dropped == 0

        # Complete the new transport's handshake.
        rebuild_transport.trigger_connect_complete()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Queued message landed on the new transport.
        all_uuids = [
            m['uuid']
            for batch in rebuild_transport.batches
            for m in batch
        ]
        assert 'u-rb' in all_uuids
        assert rebuild_transport.dropped == 0
        await handle.teardown()


@pytest.mark.asyncio
async def test_teardown_result_write_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test per CRITIC MAJOR-1.

    Teardown's result-write must be bounded so a stuck queue can't block
    teardown past ``gracefulShutdown``'s 2s budget.
    """
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    params, ctx, _ = _make_params(transport)

    # Make transport.write() block forever to simulate back-pressure.
    async def _hang(_message: dict[str, Any]) -> None:
        await asyncio.sleep(60)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        # Patch ONLY the post-init write so the orchestrator's pre-flight
        # writes succeed. ``monkeypatch.setattr`` auto-undoes the patch
        # at test end.
        monkeypatch.setattr(transport, 'write', _hang)

        # Teardown should NOT take 60s — the bounded wait_for caps it.
        loop = asyncio.get_running_loop()
        start = loop.time()
        await handle.teardown()
        elapsed = loop.time() - start
        # Generous slack but well under the 30s default producer timeout.
        assert elapsed < 2.0, f'teardown took {elapsed}s (must be < 2s)'


@pytest.mark.asyncio
async def test_mid_flush_401_re_flushes_history() -> None:
    """Regression test per CRITIC Q7.

    If a 401 SSE close fires WHILE the initial-history flush is in
    progress, the orchestrator resets ``initial_flush_done`` and the new
    transport's ``_on_connect`` re-flushes the history (so no messages
    are lost in the gap).
    """
    initial_transport = FakeTransport()
    rebuild_transport = FakeTransport()
    handler, _ = _make_http_handler()
    transports = [initial_transport, rebuild_transport]
    factory_calls = [0]

    def factory(opts: V2TransportOptions) -> Any:
        async def _make() -> FakeTransport:
            t = transports[factory_calls[0]]
            factory_calls[0] += 1
            return t
        return _make()

    async def on_auth_401(_stale: str) -> bool:
        return True

    initial = [
        UserMessage(content='h1', uuid='h-1'),
        UserMessage(content='h2', uuid='h-2'),
    ]
    params = EnvLessBridgeParams(
        base_url='https://api.example.com', org_uuid='org-1', title='t',
        get_access_token=lambda: 'oauth-tok',
        initial_history_cap=200, on_auth_401=on_auth_401,
        initial_messages=initial,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=factory,
        )
        assert handle is not None
        # Initial flush wrote history to the first transport.
        await asyncio.sleep(0)
        first_batch_uuids = [
            m['uuid'] for batch in initial_transport.batches for m in batch
        ]
        assert 'h-1' in first_batch_uuids and 'h-2' in first_batch_uuids

        # Trigger 401 → recovery → rebuild.
        initial_transport.trigger_close(401)
        for _ in range(20):
            await asyncio.sleep(0.01)
            if factory_calls[0] == 2:
                break

        # The new transport's on_connect already fired (FakeTransport
        # defaults to non-deferred). It should have re-flushed history.
        await asyncio.sleep(0)
        rebuild_batch_uuids = [
            m['uuid'] for batch in rebuild_transport.batches for m in batch
        ]
        assert 'h-1' in rebuild_batch_uuids
        assert 'h-2' in rebuild_batch_uuids
        await handle.teardown()


@pytest.mark.asyncio
async def test_state_changes_fire_in_order() -> None:
    transport = FakeTransport()
    handler, _ = _make_http_handler()
    state_log: list[Any] = []
    params, ctx, _ = _make_params(
        transport,
        on_state_change=lambda *a: state_log.append(a),
    )
    ctx['state_changes'] = state_log  # rewire

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        handle = await init_env_less_bridge_core(
            params, http_client=client, config=_short_config(),
            transport_factory=ctx['transport_factory'],
        )
        assert handle is not None
        await handle.teardown()

    # ready fires before connected.
    ready_idx = state_log.index(('ready',))
    connected_idx = state_log.index(('connected',))
    assert ready_idx < connected_idx
