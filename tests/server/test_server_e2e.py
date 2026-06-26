"""End-to-end test: DirectConnectServer + DirectConnectSessionManager.

Spins up the real server, connects via the client, exchanges a user
prompt + assistant response, verifies the session lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest

from src.server.direct_connect_manager import (
    DirectConnectCallbacks,
    DirectConnectSessionManager,
)
from src.server.direct_connect_session import (
    DirectConnectConfig,
    create_direct_connect_session,
)
from src.server.server import AgentHandle, DirectConnectServer
from src.server.session_index import load_index
from src.server.session_manager import SessionManager
from src.server.types import ServerConfig


pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _make_fake_agent_factory(scripted_responses: list[dict]):
    """Returns a SpawnAgent callable; the spawned agent emits ``scripted_responses``
    when the client sends its first message."""

    async def spawn(session_id: str, cwd: str, perm_mode: str | None) -> AgentHandle:
        send_queue: asyncio.Queue[dict] = asyncio.Queue()
        out_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        first_received = asyncio.Event()

        async def consumer() -> None:
            await send_queue.get()
            first_received.set()
            for resp in scripted_responses:
                await out_queue.put(resp)
            await out_queue.put(None)  # sentinel: shutdown

        consumer_task = asyncio.get_running_loop().create_task(consumer())

        async def send_to_agent(msg: dict) -> None:
            await send_queue.put(msg)

        async def messages_from_agent() -> AsyncIterator[dict]:
            while True:
                item = await out_queue.get()
                if item is None:
                    return
                yield item

        async def shutdown() -> None:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

        return AgentHandle(
            send_to_agent=send_to_agent,
            messages_from_agent=messages_from_agent,
            shutdown=shutdown,
        )

    return spawn


@pytest.mark.asyncio
async def test_e2e_create_session_and_exchange_message(tmp_path):
    config = ServerConfig(
        host='127.0.0.1',
        port=_free_port(),
        workspace=str(tmp_path),
    )
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / 'idx.json')
    spawn = _make_fake_agent_factory([
        {'type': 'assistant', 'uuid': 'a1', 'message': {'content': 'hi back'}},
    ])
    server = DirectConnectServer(config=config, manager=manager, spawn_agent=spawn)
    await server.start()

    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())

    try:
        # Step 1: POST /sessions via the client helper.
        cfg, work_dir = await create_direct_connect_session(
            server_url=f'http://127.0.0.1:{config.port}',
            cwd=str(tmp_path),
        )
        assert cfg.session_id.startswith('ds_')
        assert work_dir == str(tmp_path)

        # Session should be in the index.
        idx = load_index(tmp_path / 'idx.json')
        assert cfg.session_id in idx

        # Step 2: open the WS via DirectConnectSessionManager.
        received_messages: list[dict] = []
        disc = asyncio.Event()
        callbacks = DirectConnectCallbacks(
            on_message=lambda m: received_messages.append(m),
            on_permission_request=lambda req, rid: None,
            on_disconnected=lambda: disc.set(),
        )
        client = DirectConnectSessionManager(cfg, callbacks)
        await client.connect()
        try:
            # Step 3: send a user prompt; expect the scripted assistant
            # response to land via on_message.
            await client.send_message('hello')

            # Wait for the assistant message (with timeout).
            for _ in range(50):
                if received_messages:
                    break
                await asyncio.sleep(0.05)
            assert received_messages, 'expected at least one assistant message'
            assert received_messages[0]['type'] == 'assistant'
            assert received_messages[0]['message']['content'] == 'hi back'
        finally:
            await client.disconnect()

    finally:
        await server.stop()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_e2e_session_persists_across_index_reads(tmp_path):
    """After session create, the index has the entry; after stop, it's gone."""
    config = ServerConfig(
        host='127.0.0.1', port=_free_port(), workspace=str(tmp_path),
    )
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / 'idx.json')
    server = DirectConnectServer(
        config=config,
        manager=manager,
        spawn_agent=_make_fake_agent_factory([]),
    )
    await server.start()
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())

    try:
        cfg, _ = await create_direct_connect_session(
            server_url=f'http://127.0.0.1:{config.port}',
            cwd=str(tmp_path),
        )
        # Session is in the index.
        assert cfg.session_id in load_index(tmp_path / 'idx.json')

        # Stop it via the manager.
        await manager.stop_session(cfg.session_id)
        assert cfg.session_id not in load_index(tmp_path / 'idx.json')
    finally:
        await server.stop()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_e2e_unauthorized_session_create_returns_401(tmp_path):
    config = ServerConfig(
        host='127.0.0.1',
        port=_free_port(),
        auth_token='secret',
        workspace=str(tmp_path),
    )
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / 'idx.json')
    server = DirectConnectServer(
        config=config, manager=manager,
        spawn_agent=_make_fake_agent_factory([]),
    )
    await server.start()
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())

    try:
        # No auth_token passed; server requires one → 401.
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f'http://127.0.0.1:{config.port}/sessions',
                json={'cwd': str(tmp_path)},
            )
        assert resp.status_code == 401
    finally:
        await server.stop()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_e2e_unknown_route_returns_404(tmp_path):
    config = ServerConfig(host='127.0.0.1', port=_free_port(), workspace=str(tmp_path))
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / 'idx.json')
    server = DirectConnectServer(
        config=config, manager=manager,
        spawn_agent=_make_fake_agent_factory([]),
    )
    await server.start()
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f'http://127.0.0.1:{config.port}/nope')
        assert resp.status_code == 404
    finally:
        await server.stop()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_ws_accepts_global_launcher_token_like_real_client(tmp_path):
    """The real openclaude TS client keeps the launcher's *global* token and
    sends it as the WS ``Authorization: Bearer`` header — it never reads the
    per-session ``auth_token`` from the POST /sessions response. The server must
    accept that (critic B1a / migration plan), not only the per-session
    query-param token the Python port uses. A wrong token is still rejected.
    """
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import WebSocketException

    global_token = 'launcher-global-token'
    config = ServerConfig(
        host='127.0.0.1', port=_free_port(),
        auth_token=global_token, workspace=str(tmp_path),
    )
    manager = SessionManager(workspace=str(tmp_path), index_path=tmp_path / 'idx.json')
    spawn = _make_fake_agent_factory([
        {'type': 'assistant', 'uuid': 'a1', 'message': {'content': 'ok'}},
    ])
    server = DirectConnectServer(config=config, manager=manager, spawn_agent=spawn)
    await server.start()
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())
    try:
        # POST /sessions with the GLOBAL token, as the launcher + openclaude do.
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f'http://127.0.0.1:{config.port}/sessions',
                headers={'authorization': f'Bearer {global_token}',
                         'content-type': 'application/json'},
                content=json.dumps({'cwd': str(tmp_path)}),
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body['auth_token'] != global_token  # server minted a distinct T_s
        ws_base = body['ws_url'].split('?', 1)[0]   # drop the per-session ?token

        # (1) Real-client pattern: Bearer <global> header, NO per-session token.
        async with ws_connect(
            ws_base, additional_headers={'authorization': f'Bearer {global_token}'},
        ) as ws:
            await ws.send(json.dumps({'type': 'user', 'message': {'content': 'hi'}}))
            got = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert got['type'] == 'assistant' and got['message']['content'] == 'ok'

        # (2) Wrong token, no valid query param → rejected (server closes 1008).
        with pytest.raises(WebSocketException):
            async with ws_connect(
                ws_base, additional_headers={'authorization': 'Bearer wrong'},
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
    finally:
        await server.stop()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
