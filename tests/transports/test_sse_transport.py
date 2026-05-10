"""Tests for ``src.transports.sse_transport.SSETransport``.

Uses ``httpx.MockTransport`` to inject SSE-formatted responses and
asserts the parsed event flow.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.bridge.close_codes import WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED
from src.transports.sse_transport import SSEEvent, SSETransport


def _sse_response(events: list[tuple[str | None, str]]) -> bytes:
    """Build a fake SSE response body. Each event is ``(id, data)``."""
    out: list[str] = []
    for eid, data in events:
        if eid is not None:
            out.append(f'id: {eid}')
        out.append(f'data: {data}')
        out.append('')  # blank line terminates the event
    return ('\n'.join(out) + '\n').encode('utf-8')


@pytest.mark.asyncio
async def test_basic_event_dispatch():
    body = _sse_response([
        ('1', 'first'),
        ('2', 'second'),
    ])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=body,
        )

    received_data: list[str] = []
    received_events: list[SSEEvent] = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(
            url='https://api.test/stream',
            client=client,
        )
        sse.set_on_data(received_data.append)
        sse.set_on_event(received_events.append)
        await sse.connect()
        # Allow the read-loop to drain the (already-buffered) response.
        for _ in range(50):
            if len(received_data) >= 2:
                break
            await asyncio.sleep(0.02)
        await sse.aclose()

    assert received_data == ['first', 'second']
    assert [e.event_id for e in received_events] == ['1', '2']


@pytest.mark.asyncio
async def test_get_last_sequence_num_tracks_event_id():
    body = _sse_response([('100', 'a'), ('200', 'b'), ('300', 'c')])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(url='https://api.test/stream', client=client)
        sse.set_on_data(lambda _: None)
        await sse.connect()
        for _ in range(50):
            if sse.get_last_sequence_num() >= 300:
                break
            await asyncio.sleep(0.02)
        await sse.aclose()

    assert sse.get_last_sequence_num() == 300


@pytest.mark.asyncio
async def test_last_event_id_passed_on_reconnect():
    """Per Risk #21: SSETransport must pass Last-Event-ID on reconnect."""
    request_headers: list[dict] = []
    body = _sse_response([('5', 'x')])

    def handler(req: httpx.Request) -> httpx.Response:
        request_headers.append({k.lower(): v for k, v in req.headers.items()})
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(
            url='https://api.test/stream',
            client=client,
            reconnect_delay_seconds=0.01,
            reconnect_budget=2,
        )
        sse.set_on_data(lambda _: None)
        await sse.connect()
        # Wait long enough for two reconnect attempts.
        for _ in range(100):
            if len(request_headers) >= 2:
                break
            await asyncio.sleep(0.02)
        await sse.aclose()

    # First request: no Last-Event-ID. Second: has '5'.
    assert len(request_headers) >= 2
    assert request_headers[0].get('last-event-id') is None
    assert request_headers[1].get('last-event-id') == '5'


@pytest.mark.asyncio
async def test_reconnect_budget_exhausted_fires_close():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused')

    received_close: list[int | None] = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(
            url='https://api.test/stream',
            client=client,
            reconnect_delay_seconds=0.01,
            reconnect_budget=2,
        )
        sse.set_on_close(received_close.append)
        await sse.connect()
        # Wait for the budget to be exhausted.
        for _ in range(100):
            if received_close:
                break
            await asyncio.sleep(0.02)
        await sse.aclose()

    assert received_close == [WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED]


@pytest.mark.asyncio
async def test_get_auth_headers_called_on_each_connect():
    call_count = 0

    def auth_headers() -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        return {'Authorization': f'Bearer tok-{call_count}'}

    captured_auth: list[str] = []
    body = _sse_response([('1', 'x')])

    def handler(req: httpx.Request) -> httpx.Response:
        captured_auth.append(req.headers.get('authorization', ''))
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(
            url='https://api.test/stream',
            client=client,
            get_auth_headers=auth_headers,
            reconnect_delay_seconds=0.01,
            reconnect_budget=2,
        )
        sse.set_on_data(lambda _: None)
        await sse.connect()
        for _ in range(100):
            if len(captured_auth) >= 2:
                break
            await asyncio.sleep(0.02)
        await sse.aclose()

    # Each connect attempt re-evaluates get_auth_headers (token refresh).
    assert len(captured_auth) >= 2
    assert captured_auth[0] != captured_auth[1]


@pytest.mark.asyncio
async def test_close_terminates_loop():
    body = _sse_response([('1', 'x')])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sse = SSETransport(
            url='https://api.test/stream',
            client=client,
            reconnect_delay_seconds=10,  # would block reconnect for ages
        )
        sse.set_on_data(lambda _: None)
        await sse.connect()
        await asyncio.sleep(0.05)
        await sse.aclose()  # should not hang
        assert sse.is_closed_status()
