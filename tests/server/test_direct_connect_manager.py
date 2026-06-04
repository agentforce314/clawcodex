"""Tests for ``src.server.direct_connect_manager.DirectConnectSessionManager``.

Uses an in-process WS echo server so tests run without external
infrastructure. Exercises message routing, permission round-trip,
interrupt, control-request unknown-subtype error.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.bridge.messaging import AllowResponse, DenyResponse
from src.server.direct_connect_manager import (
    DirectConnectCallbacks,
    DirectConnectSessionManager,
)
from src.server.direct_connect_session import DirectConnectConfig


pytestmark = pytest.mark.integration  # spins up real WS listeners


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _TestEnv:
    """Helper: WS server + manager wired for one test case."""

    def __init__(self) -> None:
        self.received_from_client: list[str] = []
        self.client_messages_received: list[dict] = []
        self.client_permission_requests: list[tuple[dict, str]] = []
        self.client_disconnected = asyncio.Event()
        self.server_send_queue: asyncio.Queue[str] = asyncio.Queue()

    async def server_handler(self, ws):
        async def out():
            try:
                while True:
                    line = await self.server_send_queue.get()
                    await ws.send(line)
            except websockets.exceptions.ConnectionClosed:
                return

        async def inn():
            try:
                async for raw in ws:
                    text = raw if isinstance(raw, str) else raw.decode()
                    self.received_from_client.append(text)
            except websockets.exceptions.ConnectionClosed:
                return

        out_task = asyncio.get_running_loop().create_task(out())
        in_task = asyncio.get_running_loop().create_task(inn())
        try:
            await asyncio.wait(
                [out_task, in_task], return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (out_task, in_task):
                if not t.done():
                    t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
                    pass

    def make_callbacks(self) -> DirectConnectCallbacks:
        return DirectConnectCallbacks(
            on_message=lambda m: self.client_messages_received.append(m),
            on_permission_request=lambda req, rid: self.client_permission_requests.append(
                (req, rid)
            ),
            on_disconnected=lambda: self.client_disconnected.set(),
        )


@pytest.mark.asyncio
async def test_assistant_message_forwarded_to_on_message():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        try:
            await env.server_send_queue.put(json.dumps({
                'type': 'assistant', 'uuid': 'u1', 'message': {'content': 'hi'}
            }))
            # Give the reader a turn.
            await asyncio.sleep(0.05)
            assert len(env.client_messages_received) == 1
            assert env.client_messages_received[0]['type'] == 'assistant'
        finally:
            await mgr.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_permission_request_routed_correctly():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        try:
            await env.server_send_queue.put(json.dumps({
                'type': 'control_request',
                'request_id': 'req-1',
                'request': {
                    'subtype': 'can_use_tool',
                    'tool_name': 'Bash',
                    'input': {'command': 'ls'},
                    'tool_use_id': 'tu-1',
                },
            }))
            await asyncio.sleep(0.05)
            assert len(env.client_permission_requests) == 1
            req, rid = env.client_permission_requests[0]
            assert rid == 'req-1'
            assert req['tool_name'] == 'Bash'

            # Respond allow.
            await mgr.respond_to_permission_request(
                'req-1', AllowResponse(updated_input={'command': 'ls -la'})
            )
            await asyncio.sleep(0.05)
            # Server should have received the response.
            assert any('control_response' in line for line in env.received_from_client)
            sent = json.loads(env.received_from_client[-1])
            assert sent['response']['response']['behavior'] == 'allow'
            assert sent['response']['response']['updatedInput'] == {'command': 'ls -la'}
        finally:
            await mgr.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unknown_control_request_subtype_returns_error():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        try:
            await env.server_send_queue.put(json.dumps({
                'type': 'control_request',
                'request_id': 'req-2',
                'request': {'subtype': 'set_quantum_flux'},
            }))
            await asyncio.sleep(0.05)
            # Permission callback was NOT fired.
            assert env.client_permission_requests == []
            # An error response was sent back.
            assert any(
                'control_response' in line and 'error' in line
                for line in env.received_from_client
            )
        finally:
            await mgr.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_send_message_round_trip():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        try:
            ok = await mgr.send_message('hello')
            assert ok is True
            await asyncio.sleep(0.05)
            assert len(env.received_from_client) == 1
            sent = json.loads(env.received_from_client[0])
            assert sent['type'] == 'user'
            assert sent['message']['role'] == 'user'
            assert sent['message']['content'] == 'hello'
        finally:
            await mgr.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_send_interrupt_emits_control_request():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        try:
            await mgr.send_interrupt()
            await asyncio.sleep(0.05)
            assert len(env.received_from_client) == 1
            sent = json.loads(env.received_from_client[0])
            assert sent['type'] == 'control_request'
            assert sent['request']['subtype'] == 'interrupt'
        finally:
            await mgr.disconnect()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_fires_on_disconnected():
    env = _TestEnv()
    port = _free_port()
    server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = DirectConnectConfig(
            server_url=f'http://127.0.0.1:{port}',
            session_id='s1',
            ws_url=f'ws://127.0.0.1:{port}/',
        )
        mgr = DirectConnectSessionManager(config, env.make_callbacks())
        await mgr.connect()
        await mgr.disconnect()
        # ``on_disconnected`` may fire from the reader's finally block.
        await asyncio.wait_for(env.client_disconnected.wait(), timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_send_message_returns_false_when_disconnected():
    env = _TestEnv()
    config = DirectConnectConfig(
        server_url='http://x',
        session_id='s1',
        ws_url='ws://127.0.0.1:1/',
    )
    mgr = DirectConnectSessionManager(config, env.make_callbacks())
    # Did NOT call connect — should return False without raising.
    ok = await mgr.send_message('hi')
    assert ok is False
