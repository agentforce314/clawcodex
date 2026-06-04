"""Tests for ``src.remote.remote_session_manager.RemoteSessionManager``."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.bridge.messaging import AllowResponse, DenyResponse
from src.remote.remote_session_manager import (
    RemoteSessionCallbacks,
    RemoteSessionConfig,
    RemoteSessionManager,
    create_remote_session_config,
)


pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _TestEnv:
    def __init__(self):
        self.received_messages: list[dict] = []
        self.permission_requests: list[tuple[dict, str]] = []
        self.permission_cancellations: list[tuple[str, str | None]] = []
        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.from_client: list[str] = []
        self.to_client_queue: asyncio.Queue[str] = asyncio.Queue()

    async def server_handler(self, ws):
        async def out():
            try:
                while True:
                    line = await self.to_client_queue.get()
                    await ws.send(line)
            except websockets.exceptions.ConnectionClosed:
                return

        async def inn():
            try:
                async for raw in ws:
                    self.from_client.append(raw if isinstance(raw, str) else raw.decode())
            except websockets.exceptions.ConnectionClosed:
                return

        out_t = asyncio.get_running_loop().create_task(out())
        in_t = asyncio.get_running_loop().create_task(inn())
        try:
            await asyncio.wait([out_t, in_t], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (out_t, in_t):
                if not t.done():
                    t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
                    pass

    def make_callbacks(self) -> RemoteSessionCallbacks:
        return RemoteSessionCallbacks(
            on_message=lambda m: self.received_messages.append(m),
            on_permission_request=lambda req, rid: self.permission_requests.append((req, rid)),
            on_permission_cancelled=lambda rid, tuid: self.permission_cancellations.append((rid, tuid)),
            on_connected=lambda: self.connected.set(),
            on_disconnected=lambda: self.disconnected.set(),
        )


@pytest.mark.asyncio
async def test_assistant_message_forwarded():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x', get_access_token=lambda: 'tok', org_uuid='org',
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)
        await env.to_client_queue.put(json.dumps({
            'type': 'assistant', 'message': {'content': 'hello'},
        }))
        for _ in range(50):
            if env.received_messages:
                break
            await asyncio.sleep(0.02)
        assert env.received_messages[0]['type'] == 'assistant'
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_permission_request_routed_and_response_sent():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x', get_access_token=lambda: 'tok', org_uuid='org',
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)

        # Send a can_use_tool request from the server.
        await env.to_client_queue.put(json.dumps({
            'type': 'control_request',
            'request_id': 'r1',
            'request': {
                'subtype': 'can_use_tool',
                'tool_name': 'Bash',
                'input': {'command': 'ls'},
                'tool_use_id': 'tu1',
            },
        }))
        for _ in range(50):
            if env.permission_requests:
                break
            await asyncio.sleep(0.02)
        assert len(env.permission_requests) == 1
        req, rid = env.permission_requests[0]
        assert req['tool_name'] == 'Bash'
        assert rid == 'r1'

        # Respond allow.
        await mgr.respond_to_permission_request('r1', AllowResponse(updated_input={'command': 'ls -la'}))
        await asyncio.sleep(0.1)
        sent = json.loads(env.from_client[-1])
        assert sent['type'] == 'control_response'
        assert sent['response']['response']['behavior'] == 'allow'
        assert sent['response']['response']['updatedInput'] == {'command': 'ls -la'}
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_permission_cancel_routed_with_tool_use_id():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x', get_access_token=lambda: 'tok', org_uuid='org',
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)

        # Send the permission request first so the manager has a pending entry.
        await env.to_client_queue.put(json.dumps({
            'type': 'control_request',
            'request_id': 'r1',
            'request': {
                'subtype': 'can_use_tool',
                'tool_name': 'Bash',
                'input': {},
                'tool_use_id': 'tu_99',
            },
        }))
        for _ in range(50):
            if env.permission_requests:
                break
            await asyncio.sleep(0.02)

        # Now send a control_cancel_request; manager should fire on_permission_cancelled.
        await env.to_client_queue.put(json.dumps({
            'type': 'control_cancel_request',
            'request_id': 'r1',
        }))
        for _ in range(50):
            if env.permission_cancellations:
                break
            await asyncio.sleep(0.02)
        assert env.permission_cancellations == [('r1', 'tu_99')]
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_unknown_subtype_returns_error_response():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x', get_access_token=lambda: 'tok', org_uuid='org',
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)
        await env.to_client_queue.put(json.dumps({
            'type': 'control_request',
            'request_id': 'r2',
            'request': {'subtype': 'set_quantum_flux'},
        }))
        await asyncio.sleep(0.1)
        # Manager should have sent an error response back.
        assert any('error' in line for line in env.from_client)
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_cancel_session_no_op_in_viewer_only():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x',
            get_access_token=lambda: 'tok',
            org_uuid='org',
            viewer_only=True,
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)
        await mgr.cancel_session()  # no-op
        await asyncio.sleep(0.1)
        # Server received nothing — viewer-only suppressed the interrupt.
        assert env.from_client == []
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_cancel_session_sends_interrupt_when_not_viewer():
    env = _TestEnv()
    port = _free_port()
    ws_server = await ws_serve(env.server_handler, '127.0.0.1', port)
    try:
        config = RemoteSessionConfig(
            session_id='cse_x',
            get_access_token=lambda: 'tok',
            org_uuid='org',
            viewer_only=False,
        )
        mgr = RemoteSessionManager(config, env.make_callbacks(), base_url=f'ws://127.0.0.1:{port}')
        mgr.connect()
        await asyncio.wait_for(env.connected.wait(), timeout=2.0)
        await mgr.cancel_session()
        await asyncio.sleep(0.1)
        assert any('interrupt' in line for line in env.from_client)
        await mgr.disconnect()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_send_message_returns_false_when_not_connected():
    config = RemoteSessionConfig(
        session_id='cse_x', get_access_token=lambda: 'tok', org_uuid='org',
    )
    callbacks = RemoteSessionCallbacks(
        on_message=lambda m: None,
        on_permission_request=lambda req, rid: None,
    )
    mgr = RemoteSessionManager(config, callbacks)
    ok = await mgr.send_message('hi')
    assert ok is False


def test_create_remote_session_config_helper():
    cfg = create_remote_session_config(
        'cse_x', lambda: 'tok', 'org', viewer_only=True,
    )
    assert cfg.session_id == 'cse_x'
    assert cfg.viewer_only is True
    assert cfg.org_uuid == 'org'
