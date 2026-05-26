"""Tests for ``src.transports.hybrid_transport.HybridTransport``.

Strategy
--------
- WS read side: in-process WS server via ``websockets.asyncio.server.serve``
  (pattern from ``tests/transports/test_websocket_transport.py``).
- POST write side: ``httpx.MockTransport`` injected via the
  ``http_client=`` constructor kwarg (pattern from
  ``tests/transports/test_sse_transport.py``).
- Auth: ``monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', ...)``.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import httpx
import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.transports.hybrid_transport import (
    CLOSE_GRACE_S,
    HybridTransport,
    _convert_ws_url_to_post_url,
)
from src.transports.websocket_transport import WebSocketTransportOptions


pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _MockPostHandler:
    """httpx.MockTransport handler that captures POSTs and scripts responses."""

    def __init__(
        self,
        *,
        response_sequence: list[int] | None = None,
        default_status: int = 200,
    ) -> None:
        # Per-POST status code sequence; falls back to default after exhaust.
        self.response_sequence = list(response_sequence or [])
        self.default_status = default_status
        self.requests: list[httpx.Request] = []
        self.posted_events: list[list[dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content.decode('utf-8'))
        self.posted_events.append(body.get('events', []))
        if self.response_sequence:
            status = self.response_sequence.pop(0)
        else:
            status = self.default_status
        return httpx.Response(status, json={'ok': True})


class _ScriptedWsServer:
    """Minimal in-process WS server — pushes optional initial frames,
    keeps connection open until client closes."""

    def __init__(self, initial_frames: list[str] | None = None) -> None:
        self.initial_frames = list(initial_frames or [])
        self.connection_count = 0

    async def handler(self, ws) -> None:
        self.connection_count += 1
        for frame in self.initial_frames:
            try:
                await ws.send(frame)
            except (
                websockets.exceptions.ConnectionClosed, OSError,
            ):
                return
        try:
            async for _raw in ws:
                pass
        except (websockets.exceptions.ConnectionClosed, OSError):
            return


async def _wait_for(predicate, timeout_s: float = 2.0, step_s: float = 0.01):
    import time
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        await asyncio.sleep(step_s)
    return last


# ─── Pure helpers ──────────────────────────────────────────────────────


def test_convert_ws_url_wss_to_https() -> None:
    out = _convert_ws_url_to_post_url(
        'wss://api.example.com/v2/session_ingress/ws/sid123',
    )
    assert out == (
        'https://api.example.com/v2/session_ingress/session/sid123/events'
    )


def test_convert_ws_url_ws_to_http() -> None:
    out = _convert_ws_url_to_post_url(
        'ws://localhost:8080/v2/session_ingress/ws/sid',
    )
    assert out == 'http://localhost:8080/v2/session_ingress/session/sid/events'


def test_convert_ws_url_preserves_query() -> None:
    out = _convert_ws_url_to_post_url(
        'wss://h/v2/session_ingress/ws/sid?x=1',
    )
    assert out == 'https://h/v2/session_ingress/session/sid/events?x=1'


# ─── Write paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_non_stream_event_posts_immediately(monkeypatch) -> None:
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok-123')
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'control_request', 'uuid': 'u1'})
    await _wait_for(lambda: bool(mock.requests))
    assert len(mock.requests) == 1
    req = mock.requests[0]
    assert req.method == 'POST'
    assert req.url.scheme == 'https'
    assert req.url.path == '/v2/session_ingress/session/sid/events'
    assert req.headers['authorization'] == 'Bearer tok-123'
    assert mock.posted_events[0] == [
        {'type': 'control_request', 'uuid': 'u1'},
    ]
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_write_stream_event_buffers_then_flushes_after_timer(
    monkeypatch,
) -> None:
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'stream_event', 'uuid': 's1'})
    await t.write({'type': 'stream_event', 'uuid': 's2'})
    await t.write({'type': 'stream_event', 'uuid': 's3'})
    # Buffer not flushed yet (timer is 100ms).
    await asyncio.sleep(0.02)
    assert mock.requests == []
    # After the timer fires, all three events are POSTed together.
    await _wait_for(lambda: bool(mock.requests), timeout_s=1.0)
    assert len(mock.requests) == 1
    assert [e['uuid'] for e in mock.posted_events[0]] == ['s1', 's2', 's3']
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_write_non_stream_flushes_buffered_stream_events_first(
    monkeypatch,
) -> None:
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'stream_event', 'uuid': 's1'})
    await t.write({'type': 'stream_event', 'uuid': 's2'})
    # The control message flushes buffered stream events first.
    await t.write({'type': 'control_request', 'uuid': 'c1'})
    await _wait_for(lambda: bool(mock.requests))
    # All three in one POST, in order.
    assert len(mock.requests) == 1
    uuids = [e['uuid'] for e in mock.posted_events[0]]
    assert uuids == ['s1', 's2', 'c1']
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_write_batch_posts_all(monkeypatch) -> None:
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write_batch([
        {'type': 'a', 'uuid': 'u1'},
        {'type': 'b', 'uuid': 'u2'},
        {'type': 'c', 'uuid': 'u3'},
    ])
    await _wait_for(lambda: bool(mock.requests))
    assert len(mock.requests) == 1
    assert len(mock.posted_events[0]) == 3
    t.close()
    await client.aclose()


# ─── Retry / drop behavior ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_retries_on_5xx(monkeypatch) -> None:
    """503 → retry → 200. Two POST attempts observed."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler(response_sequence=[503, 200])
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'control_request', 'uuid': 'u1'})
    # Default uploader backoff base is 500ms — wait long enough for retry.
    await _wait_for(lambda: len(mock.requests) >= 2, timeout_s=3.0)
    assert len(mock.requests) == 2
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_post_retries_on_429(monkeypatch) -> None:
    """429 → retry → 200."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler(response_sequence=[429, 200])
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'control_request', 'uuid': 'u1'})
    await _wait_for(lambda: len(mock.requests) >= 2, timeout_s=3.0)
    assert len(mock.requests) == 2
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_post_drops_on_4xx_non_429(monkeypatch) -> None:
    """400 → permanent drop; uploader's send returns; next write succeeds."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler(response_sequence=[400, 200])
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    # First write: server returns 400 — dropped silently, no retry.
    await t.write({'type': 'a', 'uuid': 'u1'})
    await _wait_for(lambda: len(mock.requests) >= 1, timeout_s=1.0)
    assert len(mock.requests) == 1
    # Second write: server returns 200 — accepted.
    await t.write({'type': 'b', 'uuid': 'u2'})
    await _wait_for(lambda: len(mock.requests) >= 2, timeout_s=1.0)
    assert len(mock.requests) == 2
    t.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_post_skipped_when_no_session_token(monkeypatch) -> None:
    """Without ``CLAUDE_CODE_SESSION_ACCESS_TOKEN``, ``_post_once``
    returns silently. The events are effectively dropped — matches TS."""
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', raising=False)
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    await t.write({'type': 'a', 'uuid': 'u1'})
    # Let the uploader's drain run.
    await asyncio.sleep(0.1)
    # No POSTs — the auth check short-circuited.
    assert mock.requests == []
    t.close()
    await client.aclose()


# ─── Close behavior ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_drops_stream_event_buffer(monkeypatch) -> None:
    """Buffered stream events should NOT be POSTed after close."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    # Buffer 2 stream events (don't await the timer).
    await t.write({'type': 'stream_event', 'uuid': 's1'})
    await t.write({'type': 'stream_event', 'uuid': 's2'})
    # Close before the timer fires.
    t.close()
    # Wait long enough that the timer WOULD have fired had close not cancelled it.
    await asyncio.sleep(0.2)
    # Nothing posted.
    assert mock.requests == []
    await client.aclose()


@pytest.mark.asyncio
async def test_dropped_batch_count_proxies_uploader(monkeypatch) -> None:
    """Force a batch drop via persistent 5xx + max_consecutive_failures=2;
    ``transport.dropped_batch_count`` reflects the uploader's counter."""
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    mock = _MockPostHandler(default_status=503)
    client = httpx.AsyncClient(transport=httpx.MockTransport(mock))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
        max_consecutive_failures=2,
    )
    assert t.dropped_batch_count == 0
    await t.write({'type': 'a', 'uuid': 'u1'})
    # Wait for 2 attempts to fail + the batch to be dropped.
    await _wait_for(lambda: t.dropped_batch_count >= 1, timeout_s=5.0)
    assert t.dropped_batch_count == 1
    t.close()
    await client.aclose()


# ─── Inherited WS read path still works ────────────────────────────────


@pytest.mark.asyncio
async def test_inherited_websocket_read_path_still_works() -> None:
    """``HybridTransport`` doesn't override read-side behavior; the
    parent ``WebSocketTransport`` reader must still fire ``on_data``
    for inbound frames."""
    wss = _ScriptedWsServer(initial_frames=['inbound-frame-1'])
    port = _free_port()
    ws_server = await ws_serve(wss.handler, '127.0.0.1', port)
    try:
        received: list[str] = []
        t = HybridTransport(
            url=f'ws://127.0.0.1:{port}/v2/session_ingress/ws/sid',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        t.set_on_data(received.append)
        await t.connect()
        await _wait_for(lambda: received == ['inbound-frame-1'])
        assert received == ['inbound-frame-1']
        assert wss.connection_count == 1
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_close_grace_does_not_block_caller(monkeypatch) -> None:
    """``close()`` must return synchronously even with a slow POST in
    flight — the grace flush is fire-and-forget. We use a slow
    ``httpx.MockTransport`` handler that holds the POST until a
    release event so the test cleans up promptly without waiting
    out the full ``CLOSE_GRACE_S`` budget.
    """
    import time

    monkeypatch.setenv('CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'tok')
    release = asyncio.Event()

    async def slow_handler(_request: httpx.Request) -> httpx.Response:
        # Hold the POST until the test releases us at teardown.
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass
        return httpx.Response(200, json={'ok': True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    t = HybridTransport(
        url='wss://api.test/v2/session_ingress/ws/sid',
        options=WebSocketTransportOptions(auto_reconnect=False),
        http_client=client,
    )
    # write() awaits the uploader's flush, which won't return while
    # the POST is held by slow_handler — fire-and-forget the write task.
    write_task = asyncio.create_task(
        t.write({'type': 'a', 'uuid': 'u1'}),
        name='test-stuck-write',
    )
    # Yield so the drain enters the stuck POST.
    await asyncio.sleep(0.05)
    # Now close — must return immediately even though drain is stuck.
    start = time.monotonic()
    t.close()
    elapsed = time.monotonic() - start
    assert elapsed < CLOSE_GRACE_S * 0.5, (
        f'close() should return sync, but took {elapsed:.3f}s'
    )
    # Cleanup: release the slow handler + cancel the orphan write
    # task so the test loop drains cleanly without waiting
    # CLOSE_GRACE_S.
    release.set()
    write_task.cancel()
    try:
        await write_task
    except (asyncio.CancelledError, Exception):
        pass
    # Give the grace task a moment to observe the released event
    # and exit through uploader.close().
    await asyncio.sleep(0.05)
    await client.aclose()
