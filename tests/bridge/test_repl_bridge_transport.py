"""Tests for ``src.bridge.repl_bridge_transport`` (v2 transport).

End-to-end-ish: builds a v2 transport against an in-process httpx
mock that serves both the SSE stream and the CCR write endpoints.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.bridge.close_codes import WS_CLOSE_EPOCH_MISMATCH
from src.bridge.repl_bridge_transport import (
    V2TransportOptions,
    create_v1_repl_transport,
    create_v2_repl_transport,
)


def _sse_body(events: list[tuple[str, str]]) -> bytes:
    out: list[str] = []
    for eid, data in events:
        out.append(f'id: {eid}')
        out.append(f'data: {data}')
        out.append('')
    return ('\n'.join(out) + '\n').encode('utf-8')


# ─── Phase 14c: v1 adapter ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_v1_adapter_passes_through_to_hybrid(monkeypatch) -> None:
    """The v1 adapter is a thin pass-through over HybridTransport;
    verify the read-side callback wiring (set_on_data) is forwarded."""
    from src.transports.hybrid_transport import HybridTransport
    from src.transports.websocket_transport import WebSocketTransportOptions

    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    hybrid = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    v1 = create_v1_repl_transport(hybrid)
    received: list[str] = []
    v1.set_on_data(received.append)
    # Hybrid is closed/idle; on_data wiring is verifiable via the
    # underlying hybrid attribute (delegate confirmed).
    assert hybrid._on_data_cb is not None
    # State-label / connected-status pass-throughs.
    assert v1.get_state_label() == 'idle'
    assert v1.is_connected_status() is False
    v1.close()
    assert v1.is_connected_status() is False


@pytest.mark.asyncio
async def test_v1_adapter_get_last_sequence_num_is_zero() -> None:
    """v1 doesn't use SSE seq nums; always returns 0 (mirrors TS
    replBridgeTransport.ts:94)."""
    from src.transports.hybrid_transport import HybridTransport
    from src.transports.websocket_transport import WebSocketTransportOptions

    hybrid = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    v1 = create_v1_repl_transport(hybrid)
    assert v1.get_last_sequence_num() == 0
    v1.close()


@pytest.mark.asyncio
async def test_v1_adapter_v2_only_methods_are_noops() -> None:
    """report_state / report_metadata / report_delivery are v2-only;
    in v1 they must be no-ops (don't raise)."""
    from src.transports.hybrid_transport import HybridTransport
    from src.transports.websocket_transport import WebSocketTransportOptions

    hybrid = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    v1 = create_v1_repl_transport(hybrid)
    v1.report_state({'requires_action': True})
    v1.report_metadata({'key': 'value'})
    v1.report_delivery('event-id', 'processed')
    v1.close()


@pytest.mark.asyncio
async def test_v1_adapter_dropped_batch_count_proxies(monkeypatch) -> None:
    """``dropped_batch_count`` delegates to the hybrid's uploader."""
    from src.transports.hybrid_transport import HybridTransport
    from src.transports.websocket_transport import WebSocketTransportOptions

    hybrid = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    v1 = create_v1_repl_transport(hybrid)
    assert v1.dropped_batch_count == 0
    # Simulate a drop by bumping the underlying counter.
    hybrid._uploader._dropped_batches = 3
    assert v1.dropped_batch_count == 3
    v1.close()


@pytest.mark.asyncio
async def test_v1_adapter_connect_schedules_task_without_blocking() -> None:
    """``connect()`` is sync (per Protocol); schedules the hybrid's
    async ``connect()`` as a fire-and-forget task. Match the v2
    factory's equivalent pattern.

    Targets a bound-then-released local port so ``connect`` gets
    ECONNREFUSED instantly (no privileged-port quirks, no silent
    drops in restrictive-network CI)."""
    import socket as _socket
    from src.transports.hybrid_transport import HybridTransport
    from src.transports.websocket_transport import WebSocketTransportOptions

    # Bind to a free port, then close the socket immediately — the
    # port is now unbound and a connect() to it gets ECONNREFUSED.
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    hybrid = HybridTransport(
        url=f'ws://127.0.0.1:{port}',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    v1 = create_v1_repl_transport(hybrid)
    # connect() must return immediately even with a doomed target.
    v1.connect()
    # Give the scheduled task a tick to run (and fail).
    await asyncio.sleep(0.1)
    # Transport ends up in 'closed' state because auto_reconnect=False.
    assert hybrid.is_closed_status()
    v1.close()


@pytest.mark.asyncio
async def test_v2_transport_reads_sse_and_writes_to_ccr():
    received_writes: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith('/worker/events/stream'):
            return httpx.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content=_sse_body([('1', '{"type":"user","uuid":"u1"}')]),
            )
        if req.url.path.endswith('/worker/events'):
            import json
            body = json.loads(req.content)
            received_writes.extend(body.get('events', []))
            return httpx.Response(200, json={})
        # Heartbeat / state / delivery — all 200 OK.
        return httpx.Response(200, json={})

    received_data: list[str] = []
    connected = asyncio.Event()

    # Create transport with mocked HTTP. We have to inject the client
    # into BOTH the SSE and CCR layers, so we patch them after
    # construction.
    transport = await create_v2_repl_transport(V2TransportOptions(
        session_url='https://api.test/v1/code/sessions/cse_abc',
        ingress_token='tok',
        session_id='cse_abc',
        epoch=1,
        heartbeat_interval_seconds=0,
    ))
    transport._sse._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._sse._owned_client = True
    transport._ccr._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._ccr._owned_http = True

    transport.set_on_data(received_data.append)
    transport.set_on_connect(connected.set)
    try:
        transport.connect()
        await asyncio.wait_for(connected.wait(), timeout=2.0)
        # Write something via the v2 transport.
        await transport.write({'type': 'assistant', 'uuid': 'a1'})
        await transport.flush()
        # Wait for the SSE stream to deliver the user message.
        for _ in range(100):
            if received_data:
                break
            await asyncio.sleep(0.02)
        assert any('"type":"user"' in d for d in received_data)
        assert any(e.get('uuid') == 'a1' for e in received_writes)
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_v2_transport_409_fires_on_close_with_4090():
    """When CCR returns 409 epoch-mismatch, on_close fires with 4090."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith('/worker/events/stream'):
            return httpx.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content=_sse_body([]),
            )
        if req.url.path.endswith('/worker/events'):
            return httpx.Response(409, json={'error': 'epoch superseded'})
        return httpx.Response(200, json={})

    close_codes: list[int | None] = []
    connected = asyncio.Event()

    transport = await create_v2_repl_transport(V2TransportOptions(
        session_url='https://api.test/v1/code/sessions/cse',
        ingress_token='tok',
        session_id='cse',
        epoch=1,
        heartbeat_interval_seconds=0,
    ))
    transport._sse._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._sse._owned_client = True
    transport._ccr._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._ccr._owned_http = True

    transport.set_on_close(close_codes.append)
    transport.set_on_connect(connected.set)
    try:
        transport.connect()
        await asyncio.wait_for(connected.wait(), timeout=2.0)
        await transport.write({'type': 'user', 'uuid': 'u1'})
        # Wait for the uploader to hit 409 and fire the epoch-mismatch handler.
        for _ in range(200):
            if WS_CLOSE_EPOCH_MISMATCH in close_codes:
                break
            await asyncio.sleep(0.02)
    finally:
        await transport.aclose()

    assert WS_CLOSE_EPOCH_MISMATCH in close_codes


@pytest.mark.asyncio
async def test_v2_transport_outbound_only_skips_sse():
    """outbound_only=True: set_on_data is no-op; get_last_sequence_num returns 0."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith('/worker/events/stream'):
            # Should NOT be hit when outbound_only=True.
            raise AssertionError('SSE stream opened in outbound_only mode')
        return httpx.Response(200, json={})

    connected = asyncio.Event()

    transport = await create_v2_repl_transport(V2TransportOptions(
        session_url='https://api.test/v1/code/sessions/cse',
        ingress_token='tok',
        session_id='cse',
        epoch=1,
        outbound_only=True,
        heartbeat_interval_seconds=0,
    ))
    transport._ccr._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._ccr._owned_http = True

    transport.set_on_connect(connected.set)
    transport.set_on_data(lambda _: None)  # should be no-op
    try:
        transport.connect()
        await asyncio.wait_for(connected.wait(), timeout=2.0)
        assert transport.get_last_sequence_num() == 0
        assert transport.is_connected_status()
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_v2_transport_dropped_batch_count_passthrough():
    """Transport's dropped_batch_count delegates to CCRClient."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith('/worker/events/stream'):
            return httpx.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content=_sse_body([]),
            )
        # All writes return 503 → drops accumulate.
        if req.url.path.endswith('/worker/events'):
            return httpx.Response(503)
        return httpx.Response(200, json={})

    connected = asyncio.Event()

    transport = await create_v2_repl_transport(V2TransportOptions(
        session_url='https://api.test/v1/code/sessions/cse',
        ingress_token='tok',
        session_id='cse',
        epoch=1,
        heartbeat_interval_seconds=0,
    ))
    transport._sse._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._sse._owned_client = True
    transport._ccr._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport._ccr._owned_http = True
    transport._ccr._options.max_retries_per_batch = 1
    transport._ccr._options.retry_backoff_seconds = 0.01
    transport.set_on_connect(connected.set)
    try:
        transport.connect()
        await asyncio.wait_for(connected.wait(), timeout=2.0)
        await transport.write({'type': 'user', 'uuid': 'u1'})
        for _ in range(200):
            if transport.dropped_batch_count > 0:
                break
            await asyncio.sleep(0.02)
        assert transport.dropped_batch_count > 0
    finally:
        await transport.aclose()
