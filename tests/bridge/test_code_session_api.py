"""Tests for ``src.bridge.code_session_api``."""

from __future__ import annotations

import json

import httpx
import pytest

from src.bridge.code_session_api import (
    create_code_session,
    fetch_remote_credentials,
    register_worker,
)


# ─── create_code_session ────────────────────────────────────────────────


class TestCreateCodeSession:
    @pytest.mark.asyncio
    async def test_happy_path_returns_session_id(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.method == 'POST'
            assert req.url.path == '/v1/code/sessions'
            body = json.loads(req.content)
            assert body['title'] == 'My Session'
            assert body['bridge'] == {}
            return httpx.Response(201, json={'session': {'id': 'cse_abc'}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sid = await create_code_session(
                'https://api.test', 'tok', 'My Session', client=client,
            )
        assert sid == 'cse_abc'

    @pytest.mark.asyncio
    async def test_passes_tags_when_provided(self):
        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(201, json={'session': {'id': 'cse_xyz'}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await create_code_session(
                'https://api.test', 'tok', 'Title', tags=['a', 'b'], client=client,
            )
        assert captured[0]['tags'] == ['a', 'b']

    @pytest.mark.asyncio
    async def test_omits_tags_when_empty(self):
        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(201, json={'session': {'id': 'cse_xyz'}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await create_code_session(
                'https://api.test', 'tok', 'Title', tags=[], client=client,
            )
        assert 'tags' not in captured[0]

    @pytest.mark.asyncio
    async def test_non_2xx_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'unavailable')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sid = await create_code_session('https://api.test', 'tok', 't', client=client)
        assert sid is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('refused')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sid = await create_code_session('https://api.test', 'tok', 't', client=client)
        assert sid is None

    @pytest.mark.asyncio
    async def test_missing_session_id_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={'session': {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sid = await create_code_session('https://api.test', 'tok', 't', client=client)
        assert sid is None

    @pytest.mark.asyncio
    async def test_non_cse_prefix_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={'session': {'id': 'wrong_prefix'}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sid = await create_code_session('https://api.test', 'tok', 't', client=client)
        assert sid is None


# ─── fetch_remote_credentials ───────────────────────────────────────────


class TestFetchRemoteCredentials:
    @pytest.mark.asyncio
    async def test_happy_path_returns_credentials(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == '/v1/code/sessions/cse_abc/bridge'
            return httpx.Response(200, json={
                'worker_jwt': 'jwt-tok',
                'api_base_url': 'https://api.test',
                'expires_in': 3600,
                'worker_epoch': 5,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            creds = await fetch_remote_credentials(
                'cse_abc', 'https://api.test', 'tok', client=client,
            )
        assert creds is not None
        assert creds.worker_jwt == 'jwt-tok'
        assert creds.expires_in == 3600
        assert creds.worker_epoch == 5

    @pytest.mark.asyncio
    async def test_handles_string_encoded_int64_epoch(self):
        """Per A12 + critic: protojson sometimes serializes int64 as string."""
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                'worker_jwt': 'jwt',
                'api_base_url': 'https://api.test',
                'expires_in': 60,
                'worker_epoch': '42',
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            creds = await fetch_remote_credentials(
                'cse', 'https://api.test', 'tok', client=client,
            )
        assert creds is not None
        assert creds.worker_epoch == 42

    @pytest.mark.asyncio
    async def test_passes_trusted_device_token_header(self):
        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(dict(req.headers))
            return httpx.Response(200, json={
                'worker_jwt': 'j', 'api_base_url': 'u',
                'expires_in': 1, 'worker_epoch': 0,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_remote_credentials(
                'cse', 'https://api.test', 'tok',
                trusted_device_token='td-tok', client=client,
            )
        assert captured[0]['x-trusted-device-token'] == 'td-tok'

    @pytest.mark.asyncio
    async def test_missing_worker_jwt_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                'api_base_url': 'u', 'expires_in': 1, 'worker_epoch': 0,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            creds = await fetch_remote_credentials(
                'cse', 'https://api.test', 'tok', client=client,
            )
        assert creds is None

    @pytest.mark.asyncio
    async def test_invalid_epoch_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                'worker_jwt': 'j', 'api_base_url': 'u',
                'expires_in': 1, 'worker_epoch': 'not a number',
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            creds = await fetch_remote_credentials(
                'cse', 'https://api.test', 'tok', client=client,
            )
        assert creds is None

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, content=b'unauthorized')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            creds = await fetch_remote_credentials(
                'cse', 'https://api.test', 'tok', client=client,
            )
        assert creds is None


# ─── register_worker ───────────────────────────────────────────────────


class TestRegisterWorker:
    @pytest.mark.asyncio
    async def test_returns_int_epoch(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path.endswith('/worker/register')
            return httpx.Response(200, json={'worker_epoch': 7})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            epoch = await register_worker(
                'https://api.test/v1/code/sessions/cse_abc', 'tok', client=client,
            )
        assert epoch == 7

    @pytest.mark.asyncio
    async def test_string_encoded_epoch_coerced(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'worker_epoch': '99'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            epoch = await register_worker(
                'https://api.test/v1/code/sessions/cse', 'tok', client=client,
            )
        assert epoch == 99

    @pytest.mark.asyncio
    async def test_missing_epoch_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match='missing worker_epoch'):
                await register_worker('https://x', 'tok', client=client)

    @pytest.mark.asyncio
    async def test_non_2xx_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match='unexpected status'):
                await register_worker('https://x', 'tok', client=client)

    @pytest.mark.asyncio
    async def test_network_error_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('refused')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match='register_worker failed'):
                await register_worker('https://x', 'tok', client=client)
