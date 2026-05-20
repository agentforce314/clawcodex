"""Tests for ``src.assistant.session_history``.

Covers the contract laid out in
``my-docs/get-parity-by-folder/assistant-gap-analysis.md`` §7.4 and
``assistant-refactoring-plan.md`` §3.2.
"""

from __future__ import annotations

import httpx
import pytest

from src.assistant.session_history import (
    HISTORY_PAGE_SIZE,
    HistoryAuthCtx,
    HistoryPage,
    create_history_auth_ctx,
    fetch_latest_events,
    fetch_older_events,
)


# ─── create_history_auth_ctx ────────────────────────────────────────────


class TestCreateHistoryAuthCtx:
    @pytest.mark.asyncio
    async def test_url_includes_session_id(self):
        ctx = await create_history_auth_ctx(
            'sess_abc', access_token='tok', org_uuid='org_1',
        )
        assert ctx.base_url == 'https://api.anthropic.com/v1/sessions/sess_abc/events'

    @pytest.mark.asyncio
    async def test_default_base_url_exact(self):
        ctx = await create_history_auth_ctx(
            'sess_abc', access_token='tok', org_uuid='org_1',
        )
        # Pin the exact default — if anyone changes it, the regression
        # shows up here, not silently in prod.
        assert ctx.base_url == 'https://api.anthropic.com/v1/sessions/sess_abc/events'

    @pytest.mark.asyncio
    async def test_custom_base_url_trims_trailing_slash(self):
        ctx = await create_history_auth_ctx(
            'sess_x', access_token='t', org_uuid='o',
            base_url='https://api.test/',
        )
        assert ctx.base_url == 'https://api.test/v1/sessions/sess_x/events'

    @pytest.mark.asyncio
    async def test_custom_base_url_no_trailing_slash(self):
        ctx = await create_history_auth_ctx(
            'sess_x', access_token='t', org_uuid='o',
            base_url='https://api.test',
        )
        assert ctx.base_url == 'https://api.test/v1/sessions/sess_x/events'

    @pytest.mark.asyncio
    async def test_headers_include_all_pinned_values(self):
        ctx = await create_history_auth_ctx(
            'sess_1', access_token='my_tok', org_uuid='my_org',
        )
        assert ctx.headers['Authorization'] == 'Bearer my_tok'
        assert ctx.headers['Content-Type'] == 'application/json'
        assert ctx.headers['anthropic-version'] == '2023-06-01'
        assert ctx.headers['anthropic-beta'] == 'ccr-byoc-2025-07-29'
        assert ctx.headers['x-organization-uuid'] == 'my_org'


# ─── fetch_latest_events ────────────────────────────────────────────────


def _make_ctx(base_url: str = 'https://api.test') -> HistoryAuthCtx:
    """Build a HistoryAuthCtx synchronously for tests (the builder is
    pure; we don't need the async wrapper just to construct a value)."""
    return HistoryAuthCtx(
        base_url=f'{base_url}/v1/sessions/sess_t/events',
        headers={
            'Authorization': 'Bearer tok',
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01',
            'anthropic-beta': 'ccr-byoc-2025-07-29',
            'x-organization-uuid': 'org_t',
        },
    )


class TestFetchLatestEvents:
    @pytest.mark.asyncio
    async def test_happy_path_returns_history_page(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['url'] = str(req.url)
            captured['query'] = dict(req.url.params)
            return httpx.Response(
                200,
                json={
                    'data': [{'type': 'user', 'uuid': 'evt_1'}],
                    'has_more': True,
                    'first_id': 'evt_1',
                    'last_id': 'evt_1',
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)

        assert page is not None
        assert page.events == [{'type': 'user', 'uuid': 'evt_1'}]
        assert page.first_id == 'evt_1'
        assert page.has_more is True
        # Default limit is 100 and anchor_to_latest=true on the happy path.
        assert captured['query'].get('limit') == '100'
        assert captured['query'].get('anchor_to_latest') == 'true'

    @pytest.mark.asyncio
    async def test_default_limit_is_100(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['query'] = dict(req.url.params)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_latest_events(_make_ctx(), client=client)
        assert captured['query']['limit'] == '100'
        # Sanity: confirm the module-level constant is the same number.
        assert HISTORY_PAGE_SIZE == 100

    @pytest.mark.asyncio
    async def test_uses_anchor_to_latest_query_param(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['query'] = dict(req.url.params)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_latest_events(_make_ctx(), client=client)
        # httpx serializes booleans lowercase — locks the contract from
        # gap-analysis §6 risk register.
        assert captured['query']['anchor_to_latest'] == 'true'

    @pytest.mark.asyncio
    async def test_custom_limit_is_honored(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['query'] = dict(req.url.params)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_latest_events(_make_ctx(), limit=50, client=client)
        assert captured['query']['limit'] == '50'

    @pytest.mark.asyncio
    async def test_non_200_4xx_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b'not found')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is None

    @pytest.mark.asyncio
    async def test_non_200_5xx_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'unavailable')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        # 5xx must also return None — proves the "more permissive than
        # sibling teleport endpoints" contract from gap-analysis §2.1
        # bullet 1.
        assert page is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('refused')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException('slow')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is None

    @pytest.mark.asyncio
    async def test_non_json_body_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'not json')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is None

    @pytest.mark.asyncio
    async def test_body_not_a_dict_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is None

    @pytest.mark.asyncio
    async def test_data_missing_returns_empty_events(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={'has_more': False, 'first_id': None},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page == HistoryPage(events=[], first_id=None, has_more=False)

    @pytest.mark.asyncio
    async def test_data_is_null_returns_empty_events(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={'data': None, 'has_more': True, 'first_id': 'evt_2'},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        # null data is treated the same as "missing or not a list".
        assert page == HistoryPage(events=[], first_id='evt_2', has_more=True)

    @pytest.mark.asyncio
    async def test_data_not_a_list_preserves_pass_through_fields(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={'data': 'oops', 'has_more': True, 'first_id': 'evt_1'},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        # The point of this test: when data is malformed, first_id and
        # has_more must still survive (no None synthesis).
        assert page == HistoryPage(events=[], first_id='evt_1', has_more=True)

    @pytest.mark.asyncio
    async def test_has_more_missing_defaults_to_false(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'data': [], 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is not None
        assert page.has_more is False

    @pytest.mark.asyncio
    async def test_first_id_pass_through_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={'data': [], 'has_more': False, 'first_id': None},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is not None
        assert page.first_id is None

    @pytest.mark.asyncio
    async def test_first_id_pass_through_concrete(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={'data': [], 'has_more': True, 'first_id': 'evt_42'},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_latest_events(_make_ctx(), client=client)
        assert page is not None
        assert page.first_id == 'evt_42'


# ─── fetch_older_events ─────────────────────────────────────────────────


class TestFetchOlderEvents:
    @pytest.mark.asyncio
    async def test_happy_path_returns_history_page(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    'data': [{'type': 'assistant', 'uuid': 'evt_0'}],
                    'has_more': False,
                    'first_id': 'evt_0',
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_older_events(_make_ctx(), before_id='evt_x', client=client)
        assert page is not None
        assert page.events == [{'type': 'assistant', 'uuid': 'evt_0'}]
        assert page.first_id == 'evt_0'
        assert page.has_more is False

    @pytest.mark.asyncio
    async def test_uses_before_id_query_param(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['query'] = dict(req.url.params)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_older_events(_make_ctx(), before_id='evt_abc', client=client)
        assert captured['query']['before_id'] == 'evt_abc'
        assert captured['query']['limit'] == '100'
        # anchor_to_latest must NOT appear on the older-page query.
        assert 'anchor_to_latest' not in captured['query']

    @pytest.mark.asyncio
    async def test_custom_limit_is_honored(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['query'] = dict(req.url.params)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_older_events(_make_ctx(), before_id='evt_a', limit=25, client=client)
        assert captured['query']['limit'] == '25'

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b'oops')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await fetch_older_events(_make_ctx(), before_id='evt_a', client=client)
        assert page is None


# ─── Default-client (no `client` kwarg) branch ──────────────────────────


class TestDefaultClient:
    """Covers the ``client is None`` branch of ``_fetch_page`` where the
    helper builds a fresh ``httpx.AsyncClient`` itself. Tests inject the
    mock transport via monkeypatching ``httpx.AsyncClient``."""

    @pytest.mark.asyncio
    async def test_default_client_branch(self, monkeypatch):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={'data': [], 'has_more': False, 'first_id': None},
            )

        transport = httpx.MockTransport(handler)
        # Wrap the real AsyncClient so the production code can build it
        # without ever opening a real socket.
        from src.assistant import session_history as mod

        original = mod.httpx.AsyncClient

        def patched(*args, **kwargs):
            kwargs.setdefault('transport', transport)
            return original(*args, **kwargs)

        monkeypatch.setattr(mod.httpx, 'AsyncClient', patched)
        page = await fetch_latest_events(_make_ctx())  # no client= kwarg
        assert page is not None
        assert page.events == []


# ─── URL composition ────────────────────────────────────────────────────


class TestUrlCorrectness:
    @pytest.mark.asyncio
    async def test_endpoint_path_is_v1_sessions_events(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['path'] = req.url.path
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        ctx = await create_history_auth_ctx(
            'sess_xyz', access_token='t', org_uuid='o',
            base_url='https://api.test',
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_latest_events(ctx, client=client)
        assert captured['path'] == '/v1/sessions/sess_xyz/events'

    @pytest.mark.asyncio
    async def test_headers_sent_on_request(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured['headers'] = dict(req.headers)
            return httpx.Response(200, json={'data': [], 'has_more': False, 'first_id': None})

        ctx = await create_history_auth_ctx(
            'sess_y', access_token='real_tok', org_uuid='real_org',
            base_url='https://api.test',
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_latest_events(ctx, client=client)
        headers = captured['headers']
        assert headers['authorization'] == 'Bearer real_tok'
        assert headers['content-type'] == 'application/json'
        assert headers['anthropic-version'] == '2023-06-01'
        assert headers['anthropic-beta'] == 'ccr-byoc-2025-07-29'
        assert headers['x-organization-uuid'] == 'real_org'
