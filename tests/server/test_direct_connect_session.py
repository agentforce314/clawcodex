"""Tests for ``src.server.direct_connect_session``."""

from __future__ import annotations

import json

import httpx
import pytest

from src.server.direct_connect_session import (
    DirectConnectError,
    create_direct_connect_session,
)


@pytest.mark.asyncio
async def test_happy_path():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == 'POST'
        assert req.url.path == '/sessions'
        body = json.loads(req.content)
        assert body['cwd'] == '/tmp/work'
        return httpx.Response(
            201,
            json={
                'session_id': 'cse_abc',
                'ws_url': 'ws://127.0.0.1:1234/ws/cse_abc?token=tk',
                'work_dir': '/tmp/work',
                'auth_token': 'tk',
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config, work_dir = await create_direct_connect_session(
            server_url='http://srv', cwd='/tmp/work', client=client,
        )
    assert config.session_id == 'cse_abc'
    assert config.ws_url == 'ws://127.0.0.1:1234/ws/cse_abc?token=tk'
    assert work_dir == '/tmp/work'


@pytest.mark.asyncio
async def test_passes_auth_token_when_set():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get('authorization') == 'Bearer my-token'
        return httpx.Response(
            201, json={'session_id': 's', 'ws_url': 'ws://x/ws/s'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await create_direct_connect_session(
            server_url='http://srv', cwd='/tmp', auth_token='my-token', client=client,
        )


@pytest.mark.asyncio
async def test_dangerously_skip_permissions_in_body():
    captured_body: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.append(json.loads(req.content))
        return httpx.Response(
            201, json={'session_id': 's', 'ws_url': 'ws://x/ws/s'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await create_direct_connect_session(
            server_url='http://srv',
            cwd='/tmp',
            dangerously_skip_permissions=True,
            client=client,
        )
    assert captured_body[0]['dangerously_skip_permissions'] is True


@pytest.mark.asyncio
async def test_network_error_raises_direct_connect_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectConnectError, match='Failed to connect'):
            await create_direct_connect_session(
                server_url='http://srv', cwd='/tmp', client=client,
            )


@pytest.mark.asyncio
async def test_non_2xx_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'unavailable')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectConnectError, match='Failed to create session'):
            await create_direct_connect_session(
                server_url='http://srv', cwd='/tmp', client=client,
            )


@pytest.mark.asyncio
async def test_invalid_response_payload_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={'session_id': 's'})  # missing ws_url

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DirectConnectError, match='Invalid session response'):
            await create_direct_connect_session(
                server_url='http://srv', cwd='/tmp', client=client,
            )
