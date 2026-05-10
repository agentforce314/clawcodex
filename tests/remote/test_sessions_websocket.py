"""Tests for ``src.remote.sessions_websocket.SessionsWebSocket``.

Uses an in-process WS echo/script server with controllable close codes
to exercise the discriminated reconnection strategy.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.remote.sessions_websocket import (
    MAX_RECONNECT_ATTEMPTS,
    MAX_SESSION_NOT_FOUND_RETRIES,
    SessionsWebSocket,
    SessionsWebSocketCallbacks,
)


pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _ScriptedServer:
    """In-process WS server with per-connection close-code control.

    On each connection, immediately closes with the next ``close_codes``
    code. We do NOT wait for client messages — the test purpose is to
    exercise the client's reconnection logic, not message exchange.
    """

    def __init__(self, close_codes: list[int]) -> None:
        self.close_codes = list(close_codes)
        self.connection_count = 0
        self.received_messages: list[str] = []

    async def handler(self, ws):
        self.connection_count += 1
        # Close immediately with the next code — don't wait for messages.
        if self.close_codes:
            code = self.close_codes.pop(0)
            try:
                await ws.close(code=code, reason='scripted close')
            except (websockets.exceptions.ConnectionClosed, OSError):
                pass


@pytest.mark.asyncio
async def test_4003_is_permanent_no_reconnect():
    server = _ScriptedServer(close_codes=[4003])
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        on_close_fired = asyncio.Event()
        callbacks = SessionsWebSocketCallbacks(
            on_message=lambda m: None,
            on_close=lambda: on_close_fired.set(),
        )
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        # Server immediately closes with 4003.
        await asyncio.wait_for(on_close_fired.wait(), timeout=2.0)
        # Only one connection attempt — 4003 is permanent.
        assert server.connection_count == 1
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_4001_triggers_reconnect_with_on_reconnecting():
    """A single 4001 close fires on_reconnecting then attempts to reconnect.

    NOTE: matches TS — both ``sessionNotFoundRetries`` and
    ``reconnectAttempts`` are reset on every successful WS open, so a
    server that keeps sending 4001 forever loops indefinitely. The
    realistic test is "verify the 4001 path triggers a reconnect at all"
    rather than budget exhaustion.
    """
    server = _ScriptedServer(close_codes=[4001, 4001])
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        reconnecting_fired = asyncio.Event()
        callbacks = SessionsWebSocketCallbacks(
            on_message=lambda m: None,
            on_reconnecting=lambda: reconnecting_fired.set(),
        )
        from src.remote import sessions_websocket as ws_mod
        original_delay = ws_mod.RECONNECT_DELAY_SECONDS
        ws_mod.RECONNECT_DELAY_SECONDS = 0.02
        try:
            ws = SessionsWebSocket(
                'sid', 'org', lambda: 'tok', callbacks,
                base_url=f'ws://127.0.0.1:{port}',
            )
            await ws.connect()
            await asyncio.wait_for(reconnecting_fired.wait(), timeout=5.0)
            # Wait for the second connect to happen.
            for _ in range(100):
                if server.connection_count >= 2:
                    break
                await asyncio.sleep(0.02)
            assert server.connection_count >= 2
        finally:
            ws_mod.RECONNECT_DELAY_SECONDS = original_delay
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_initial_connect_failure_exhausts_budget():
    """When the server is unreachable from the start, the initial-connect
    retry budget exhausts at MAX_RECONNECT_ATTEMPTS. This is the realistic
    "budget exhaustion" path because no successful connect resets the
    counter.
    """
    # Use a bound-then-released port so connect() fails with refused.
    port = _free_port()  # binds + releases — port is now likely refused

    on_close_fired = asyncio.Event()
    error_count = 0

    def on_error(_exc):
        nonlocal error_count
        error_count += 1

    callbacks = SessionsWebSocketCallbacks(
        on_message=lambda m: None,
        on_close=lambda: on_close_fired.set(),
        on_error=on_error,
    )
    from src.remote import sessions_websocket as ws_mod
    original_delay = ws_mod.RECONNECT_DELAY_SECONDS
    ws_mod.RECONNECT_DELAY_SECONDS = 0.02
    try:
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        await asyncio.wait_for(on_close_fired.wait(), timeout=10.0)
    finally:
        ws_mod.RECONNECT_DELAY_SECONDS = original_delay

    # Each failed initial connect goes through _schedule_reconnect_or_close
    # which increments _reconnect_attempts; budget exhausts at MAX+1.
    assert error_count >= MAX_RECONNECT_ATTEMPTS
    await ws.disconnect()


@pytest.mark.asyncio
async def test_message_dispatch_to_on_message():
    received: list[dict] = []

    class _MsgServer:
        def __init__(self):
            self.connection_count = 0

        async def handler(self, ws):
            self.connection_count += 1
            await ws.send(json.dumps({'type': 'assistant', 'message': {'content': 'hi'}}))
            await asyncio.sleep(0.1)  # keep connection open briefly

    server = _MsgServer()
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        callbacks = SessionsWebSocketCallbacks(
            on_message=lambda m: received.append(m),
        )
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)
        assert any(m.get('type') == 'assistant' for m in received)
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_invalid_json_is_silently_dropped():
    """Server sends garbage; reader logs + drops, doesn't fire on_message."""
    received: list[dict] = []

    class _GarbageServer:
        async def handler(self, ws):
            await ws.send('not json {{{')
            await ws.send(json.dumps({'type': 'good'}))
            await asyncio.sleep(0.1)

    server = _GarbageServer()
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        callbacks = SessionsWebSocketCallbacks(
            on_message=lambda m: received.append(m),
        )
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)
        # Only the good message should make it through.
        assert received == [{'type': 'good'}]
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_send_control_response_round_trip():
    server_received: list[str] = []

    class _EchoServer:
        async def handler(self, ws):
            try:
                async for raw in ws:
                    server_received.append(raw if isinstance(raw, str) else raw.decode())
            except websockets.exceptions.ConnectionClosed:
                pass

    server = _EchoServer()
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        callbacks = SessionsWebSocketCallbacks(on_message=lambda m: None)
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        await ws.send_control_response({
            'type': 'control_response',
            'response': {'subtype': 'success', 'request_id': 'r1'},
        })
        await asyncio.sleep(0.1)
        assert any('control_response' in line for line in server_received)
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_send_control_request_wraps_in_envelope():
    server_received: list[str] = []

    class _EchoServer:
        async def handler(self, ws):
            try:
                async for raw in ws:
                    server_received.append(raw if isinstance(raw, str) else raw.decode())
            except websockets.exceptions.ConnectionClosed:
                pass

    server = _EchoServer()
    port = _free_port()
    ws_server = await ws_serve(server.handler, '127.0.0.1', port)
    try:
        callbacks = SessionsWebSocketCallbacks(on_message=lambda m: None)
        ws = SessionsWebSocket(
            'sid', 'org', lambda: 'tok', callbacks,
            base_url=f'ws://127.0.0.1:{port}',
        )
        await ws.connect()
        await ws.send_control_request({'subtype': 'interrupt'})
        await asyncio.sleep(0.1)
        assert len(server_received) == 1
        envelope = json.loads(server_received[0])
        assert envelope['type'] == 'control_request'
        assert envelope['request']['subtype'] == 'interrupt'
        assert 'request_id' in envelope
        await ws.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()
