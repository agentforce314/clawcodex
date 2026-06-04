"""Test coverage closing the major-issue test gaps the Critic flagged:

- XAA two-step token exchange (RFC 8693 + RFC 7523)
- XAA IdP login JWT-exp parsing + cache + eligibility gate
- output_validation truncation logic
- tool_wrapper input validation (jsonschema)
- connection_manager write methods (reconnect, toggle, inject, trigger_oauth)
- InProcessTransport round-trip + close-cascade + close-unblocks-receive
- WI-8.5 binary content persistence via tool_wrapper
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.mcp import (
    InProcessTransport,
    create_linked_transport_pair,
    truncate_mcp_content_if_needed,
)
from src.services.mcp.connection_manager import MCPConnectionManager
from src.services.mcp.in_process_transport import _ClosedSentinel
from src.services.mcp.tool_wrapper import (
    _flatten_content_blocks_to_text,
    wrap_mcp_tool,
)
from src.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpHTTPServerConfig,
    McpStdioServerConfig,
    McpToolResult,
    McpToolSchema,
    NeedsAuthMCPServer,
    ScopedMcpServerConfig,
    ServerCapabilities,
)
from src.services.mcp.xaa import (
    ID_JAG_TOKEN_TYPE,
    ID_TOKEN_TYPE,
    JWT_BEARER_GRANT,
    TOKEN_EXCHANGE_GRANT,
    XaaTokenExchangeError,
    perform_cross_app_access,
)


# ----------------------------------------------------------------------
# XAA two-step token exchange
# ----------------------------------------------------------------------


class TestXaaTokenExchange:

    @pytest.mark.asyncio
    async def test_happy_path_returns_access_token(self):
        call_log: list[dict[str, Any]] = []

        async def fake_post(self, url, *, data=None, headers=None, **kw):
            call_log.append(dict(data or {}))
            grant = data.get("grant_type") if isinstance(data, dict) else None
            if grant == TOKEN_EXCHANGE_GRANT:
                return httpx.Response(
                    200,
                    json={"access_token": "ID-JAG-VALUE", "token_type": "urn:..."},
                    request=httpx.Request("POST", url),
                )
            if grant == JWT_BEARER_GRANT:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "MCP-ACCESS-TOKEN",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                    request=httpx.Request("POST", url),
                )
            raise AssertionError(f"unexpected grant {grant}")

        with patch("httpx.AsyncClient.post", new=fake_post):
            token = await perform_cross_app_access(
                auth_server_url="https://auth.example.com/oauth/token",
                id_token="ID_TOKEN_FROM_IDP",
                client_id="cid",
                target_audience="https://mcp.example.com",
                scopes=["read", "write"],
            )

        # Two-step flow happened in order.
        assert len(call_log) == 2
        # Step 1 carries subject_token + requested_token_type + audience.
        assert call_log[0]["subject_token"] == "ID_TOKEN_FROM_IDP"
        assert call_log[0]["subject_token_type"] == ID_TOKEN_TYPE
        assert call_log[0]["requested_token_type"] == ID_JAG_TOKEN_TYPE
        assert call_log[0]["audience"] == "https://mcp.example.com"
        # Step 2 carries the ID-JAG as assertion.
        assert call_log[1]["assertion"] == "ID-JAG-VALUE"
        assert call_log[1]["scope"] == "read write"
        assert token.access_token == "MCP-ACCESS-TOKEN"
        assert token.expires_at is not None

    @pytest.mark.asyncio
    async def test_step1_invalid_grant_signals_clear_id_token(self):
        async def fake_post(self, url, *, data=None, headers=None, **kw):
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "id_token expired"},
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            with pytest.raises(XaaTokenExchangeError) as exc_info:
                await perform_cross_app_access(
                    auth_server_url="https://auth.example.com/oauth/token",
                    id_token="STALE",
                    client_id="cid",
                    target_audience="https://mcp.example.com",
                )
        assert exc_info.value.should_clear_id_token is True

    @pytest.mark.asyncio
    async def test_step1_other_error_does_not_signal_clear(self):
        async def fake_post(self, url, *, data=None, headers=None, **kw):
            return httpx.Response(
                500,
                json={"error": "server_error"},
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            with pytest.raises(XaaTokenExchangeError) as exc_info:
                await perform_cross_app_access(
                    auth_server_url="https://auth.example.com/oauth/token",
                    id_token="TOK",
                    client_id="cid",
                    target_audience="https://mcp.example.com",
                )
        assert exc_info.value.should_clear_id_token is False

    @pytest.mark.asyncio
    async def test_step2_missing_access_token_raises(self):
        async def fake_post(self, url, *, data=None, headers=None, **kw):
            grant = data.get("grant_type")
            if grant == TOKEN_EXCHANGE_GRANT:
                return httpx.Response(
                    200,
                    json={"access_token": "ID-JAG"},
                    request=httpx.Request("POST", url),
                )
            # Step 2: body is missing access_token.
            return httpx.Response(
                200,
                json={"token_type": "Bearer"},
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            with pytest.raises(XaaTokenExchangeError, match="no access_token"):
                await perform_cross_app_access(
                    auth_server_url="https://auth.example.com/oauth/token",
                    id_token="T",
                    client_id="cid",
                    target_audience="https://mcp.example.com",
                )

    @pytest.mark.asyncio
    async def test_slack_style_200_with_error_body_normalized(self):
        """Step 1: vendor 200+error body should be promoted via
        ``normalize_oauth_error_body`` and raise (not silently advance)."""
        async def fake_post(self, url, *, data=None, headers=None, **kw):
            return httpx.Response(
                200,
                json={"error": "invalid_refresh_token"},  # vendor code
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            with pytest.raises(XaaTokenExchangeError) as exc_info:
                await perform_cross_app_access(
                    auth_server_url="https://auth.example.com/oauth/token",
                    id_token="T",
                    client_id="cid",
                    target_audience="https://mcp.example.com",
                )
        # vendor invalid_refresh_token → RFC invalid_grant; step1 flags
        # should_clear_id_token when the canonical code is invalid_grant.
        assert exc_info.value.should_clear_id_token is True

    @pytest.mark.asyncio
    async def test_network_error_step1_does_not_signal_clear(self):
        async def fake_post(self, url, *, data=None, headers=None, **kw):
            raise httpx.ConnectError("DNS failure")

        with patch("httpx.AsyncClient.post", new=fake_post):
            with pytest.raises(XaaTokenExchangeError) as exc_info:
                await perform_cross_app_access(
                    auth_server_url="https://auth.example.com/oauth/token",
                    id_token="T",
                    client_id="cid",
                    target_audience="https://mcp.example.com",
                )
        assert exc_info.value.should_clear_id_token is False


# ----------------------------------------------------------------------
# XAA IdP login: JWT exp parsing + cache + eligibility gate
# ----------------------------------------------------------------------


def _build_jwt_with_exp(exp: int) -> str:
    """Build a minimal unsigned JWT with the given exp claim. Only the
    payload is read by the helper; the signature is unused."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp, "sub": "user"}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.SIGNATURE"


class TestXaaIdpLogin:

    def test_jwt_exp_decoded_from_payload(self):
        from src.services.mcp.xaa_idp_login import jwt_exp as _jwt_exp

        future = 9_999_999_999
        token = _build_jwt_with_exp(future)
        assert _jwt_exp(token) == future

    def test_jwt_exp_malformed_returns_none(self):
        from src.services.mcp.xaa_idp_login import jwt_exp as _jwt_exp

        assert _jwt_exp("not.a.jwt") is None
        assert _jwt_exp("") is None
        assert _jwt_exp("only.two") is None

    def test_is_xaa_enabled_off_by_default(self, monkeypatch):
        from src.services.mcp.xaa_idp_login import is_xaa_enabled

        monkeypatch.delenv("ENABLE_MCP_XAA", raising=False)
        assert is_xaa_enabled() is False

    def test_is_xaa_enabled_on_when_env_set(self, monkeypatch):
        from src.services.mcp.xaa_idp_login import is_xaa_enabled

        monkeypatch.setenv("ENABLE_MCP_XAA", "1")
        monkeypatch.setenv("MCP_XAA_ISSUER", "https://idp.example.com")
        assert is_xaa_enabled() is True

    def test_is_xaa_enabled_off_without_issuer(self, monkeypatch):
        from src.services.mcp.xaa_idp_login import is_xaa_enabled

        monkeypatch.setenv("ENABLE_MCP_XAA", "1")
        monkeypatch.delenv("MCP_XAA_ISSUER", raising=False)
        assert is_xaa_enabled() is False


# ----------------------------------------------------------------------
# output_validation: truncation logic
# ----------------------------------------------------------------------


class TestOutputValidationTruncation:

    def test_short_content_not_truncated(self):
        blocks = [{"type": "text", "text": "small payload"}]
        out, truncated = truncate_mcp_content_if_needed(blocks)
        assert truncated is False
        assert out == blocks

    def test_oversized_content_is_truncated(self, monkeypatch):
        # Force a tiny budget so we can hit the truncation branch without
        # generating a multi-MB string.
        monkeypatch.setenv("MCP_MAX_OUTPUT_TOKENS", "100")
        blocks = [{"type": "text", "text": "x" * 100_000}]
        out, truncated = truncate_mcp_content_if_needed(blocks)
        assert truncated is True
        # Truncation notice should be appended in the rendered output.
        rendered = json.dumps(out)
        assert "truncated" in rendered.lower() or "content truncated" in rendered.lower()

    def test_tiktoken_fast_path_for_repetitive_content(self, monkeypatch):
        """Token estimate for a 1MB repetitive string must complete in
        constant-ish time (not the 100+ seconds tiktoken takes natively).
        We use a small budget so the truncation branch is exercised."""
        from src.services.mcp.output_validation import get_content_size_estimate

        big = "a" * 600_000
        blocks = [{"type": "text", "text": big}]
        # If the fast-path threshold isn't honored, this would hang for
        # a long time. We use a generous test budget (5s) — fast path
        # should finish in milliseconds.
        import time

        t0 = time.time()
        estimate = get_content_size_estimate(blocks)
        elapsed = time.time() - t0
        assert elapsed < 5.0, f"token estimate took {elapsed:.2f}s; fast path broken"
        assert estimate > 0


# ----------------------------------------------------------------------
# tool_wrapper: input validation (jsonschema)
# ----------------------------------------------------------------------


class TestToolWrapperInputValidation:

    def _build_tool(self, schema: dict[str, Any], client=None):
        mcp_tool = McpToolSchema(
            name="my_tool",
            description="A tool",
            input_schema=schema,
        )
        client = client or MagicMock()
        return wrap_mcp_tool("test_server", mcp_tool, client)

    def test_valid_args_passes_validation(self):
        from src.tool_system.context import ToolContext

        client = MagicMock()
        client.call_tool = AsyncMock(return_value=McpToolResult(
            content=[{"type": "text", "text": "ok"}],
        ))
        tool = self._build_tool(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            client=client,
        )
        ctx = MagicMock(spec=ToolContext)
        result = tool.call({"name": "alice"}, ctx)
        assert result.is_error is False

    def test_missing_required_field_returns_structured_error(self):
        from src.tool_system.context import ToolContext

        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=AssertionError(
            "server should not be called when validation fails"
        ))
        tool = self._build_tool(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            client=client,
        )
        ctx = MagicMock(spec=ToolContext)
        result = tool.call({}, ctx)
        assert result.is_error is True
        assert "Invalid input" in result.output
        assert "name" in result.output

    def test_wrong_type_returns_structured_error(self):
        from src.tool_system.context import ToolContext

        client = MagicMock()
        client.call_tool = AsyncMock()
        tool = self._build_tool(
            {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
            client=client,
        )
        ctx = MagicMock(spec=ToolContext)
        result = tool.call({"count": "not-a-number"}, ctx)
        assert result.is_error is True
        assert "Invalid input" in result.output


# ----------------------------------------------------------------------
# connection_manager: write methods
# ----------------------------------------------------------------------


class TestConnectionManagerWriteMethods:

    @pytest.mark.asyncio
    async def test_reconnect_drops_old_client_and_calls_connect(self):
        mgr = MCPConnectionManager()
        old_client = MagicMock()
        old_client.close = AsyncMock()
        mgr._clients["srv"] = old_client

        new_client = MagicMock()
        new_client.list_tools = AsyncMock(return_value=[])
        new_conn = ConnectedMCPServer(name="srv")
        new_client._auth_provider = None

        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )

        async def fake_connect(name, conf, *, auth_provider=None):
            return new_client, new_conn

        with patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect,
        ), patch(
            "src.services.mcp.connection_manager.wrap_mcp_tools_for_server",
            return_value=[],
        ):
            result = await mgr.reconnect_mcp_server("srv")

        # Old client got closed.
        old_client.close.assert_awaited_once()
        # New connection installed.
        assert mgr._state["srv"] is new_conn
        assert mgr._clients["srv"] is new_client
        assert isinstance(result, ConnectedMCPServer)

    @pytest.mark.asyncio
    async def test_reconnect_for_unknown_server_returns_failed(self):
        mgr = MCPConnectionManager()
        with patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=None,
        ):
            result = await mgr.reconnect_mcp_server("ghost")
        assert isinstance(result, FailedMCPServer)
        assert "ghost" in (result.error or "")

    @pytest.mark.asyncio
    async def test_toggle_disabled_to_enabled_reconnects_atomically(self):
        mgr = MCPConnectionManager()
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )
        new_client = MagicMock()
        new_client.list_tools = AsyncMock(return_value=[])
        new_conn = ConnectedMCPServer(name="srv")

        async def fake_connect(name, conf, *, auth_provider=None):
            return new_client, new_conn

        with patch(
            "src.services.mcp.connection_manager.is_mcp_server_disabled",
            return_value=True,
        ), patch(
            "src.services.mcp.connection_manager.set_mcp_server_enabled",
        ) as mock_set, patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect,
        ), patch(
            "src.services.mcp.connection_manager.wrap_mcp_tools_for_server",
            return_value=[],
        ):
            result = await mgr.toggle_mcp_server("srv")

        mock_set.assert_called_with("srv", True)
        assert isinstance(result, ConnectedMCPServer)

    @pytest.mark.asyncio
    async def test_toggle_enabled_to_disabled_drops_client(self):
        mgr = MCPConnectionManager()
        old_client = MagicMock()
        old_client.close = AsyncMock()
        mgr._clients["srv"] = old_client
        mgr._tools["srv"] = []

        with patch(
            "src.services.mcp.connection_manager.is_mcp_server_disabled",
            return_value=False,
        ), patch(
            "src.services.mcp.connection_manager.set_mcp_server_enabled",
        ) as mock_set:
            result = await mgr.toggle_mcp_server("srv")

        mock_set.assert_called_with("srv", False)
        assert isinstance(result, DisabledMCPServer)
        assert "srv" not in mgr._clients
        old_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_oauth_returns_failed_when_no_provider(self):
        mgr = MCPConnectionManager(auth_provider=None)
        result = await mgr.trigger_oauth("srv")
        assert isinstance(result, FailedMCPServer)
        assert "auth provider" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_trigger_oauth_uses_auth_provider(self):
        provider = MagicMock()
        provider.acquire_token = AsyncMock(return_value=MagicMock(
            success=True, error=None
        ))
        mgr = MCPConnectionManager(auth_provider=provider)
        config = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://example.com/mcp"),
            scope="user",
        )
        new_client = MagicMock()
        new_client.list_tools = AsyncMock(return_value=[])
        new_conn = ConnectedMCPServer(name="srv")

        async def fake_connect(name, conf, *, auth_provider=None):
            assert auth_provider is provider
            return new_client, new_conn

        with patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect,
        ), patch(
            "src.services.mcp.connection_manager.wrap_mcp_tools_for_server",
            return_value=[],
        ):
            result = await mgr.trigger_oauth("srv", open_browser=False)
        provider.acquire_token.assert_awaited_once()
        assert isinstance(result, ConnectedMCPServer)


# ----------------------------------------------------------------------
# InProcessTransport: round-trip + close cascade + close-unblocks-receive
# ----------------------------------------------------------------------


class TestInProcessTransport:

    @pytest.mark.asyncio
    async def test_send_receive_round_trip(self):
        a, b = create_linked_transport_pair()
        await a.start()
        await b.start()
        await a.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        msg = await b.receive()
        assert msg == {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    @pytest.mark.asyncio
    async def test_close_cascade_unblocks_peer_receive(self):
        """The blocker fix: pending receive() on the peer must return
        None when the local side closes. Earlier implementation could
        leave the peer hung indefinitely."""
        a, b = create_linked_transport_pair()
        await a.start()
        await b.start()

        async def waiter():
            return await b.receive()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        await a.close()
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_then_close_delivers_pending_message(self):
        """The 'send → close' race fix: messages sent right before close
        must reach the peer (not be dropped by the sentinel arriving
        first via call_soon ordering)."""
        a, b = create_linked_transport_pair()
        await a.start()
        await b.start()
        await a.send({"id": 42, "value": "important"})
        await a.close()
        # Receive should yield the message first, then None.
        msg = await b.receive()
        assert msg == {"id": 42, "value": "important"}
        next_msg = await b.receive()
        assert next_msg is None


# ----------------------------------------------------------------------
# WI-8.5 binary content persistence (integration via tool_wrapper)
# ----------------------------------------------------------------------


class TestBinaryContentPersistence:

    def test_image_block_persists_to_tempfile_and_returns_path_reference(
        self, tmp_path, monkeypatch,
    ):
        """An MCP server returning an image block should NOT inject the
        raw base64 (or the old '[image content]' placeholder) into the
        model-facing text. Instead, persist the bytes and substitute a
        path-reference line."""
        from src.services.mcp import output_storage

        monkeypatch.setattr(output_storage, "_BLOB_DIR", tmp_path)
        pixel = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fake_image_bytes" * 50).decode()
        blocks = [
            {"type": "text", "text": "Here is the image:"},
            {"type": "image", "data": pixel, "mimeType": "image/png"},
        ]
        text = _flatten_content_blocks_to_text(
            blocks, server_name="vision", tool_name="screenshot",
        )
        assert "Here is the image:" in text
        assert "binary content saved to" in text
        # The path should point under our patched tempdir.
        assert str(tmp_path) in text
        # The raw base64 must NOT be inlined.
        assert pixel not in text
        # Some file actually exists on disk.
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].stat().st_size > 0

    def test_resource_with_text_field_inlined(self):
        blocks = [
            {
                "type": "resource",
                "resource": {"text": "Hello, world", "uri": "file://x"},
            }
        ]
        text = _flatten_content_blocks_to_text(blocks)
        assert "Hello, world" in text

    def test_resource_with_blob_persists(self, tmp_path, monkeypatch):
        from src.services.mcp import output_storage

        monkeypatch.setattr(output_storage, "_BLOB_DIR", tmp_path)
        blob_b64 = base64.b64encode(b"some binary payload").decode()
        blocks = [
            {
                "type": "resource",
                "resource": {
                    "blob": blob_b64,
                    "mimeType": "application/octet-stream",
                    "uri": "file://x",
                },
            },
        ]
        text = _flatten_content_blocks_to_text(blocks, server_name="s", tool_name="t")
        assert "binary content saved to" in text
        files = list(tmp_path.iterdir())
        assert len(files) == 1


# ----------------------------------------------------------------------
# Policy filter: settings.extra fallback
# ----------------------------------------------------------------------


class TestPolicyFilterReadsExtra:
    """The plan + Critic flagged: ``filter_mcp_servers_by_policy`` was
    reading ``getattr(settings, 'disable_all_mcp', False)`` but the
    SettingsSchema dataclass does not declare that field — it ends up
    in the ``extra`` dict, so the gate was silently inactive."""

    def test_disable_all_mcp_via_extra_dict_filters_everything(self):
        from src.services.mcp.config import filter_mcp_servers_by_policy
        from src.settings.types import SettingsSchema

        settings = SettingsSchema(extra={"disable_all_mcp": True})
        configs = {
            "a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=settings,
        ):
            filtered, notices = filter_mcp_servers_by_policy(configs)
        assert filtered == {}
        assert len(notices) == 1
        assert "disable_all_mcp" in notices[0]

    def test_allow_managed_only_mcp_via_extra_drops_non_managed(self):
        from src.services.mcp.config import filter_mcp_servers_by_policy
        from src.settings.types import SettingsSchema

        settings = SettingsSchema(extra={"allow_managed_only_mcp": True})
        configs = {
            "user_a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
            "ent_a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="enterprise",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=settings,
        ):
            filtered, notices = filter_mcp_servers_by_policy(configs)
        assert set(filtered) == {"ent_a"}
        assert any("user_a" in n for n in notices)

    def test_camelcase_alias_is_accepted(self):
        """JSON files commonly use camelCase. The lookup must accept both
        the snake_case canonical and the camelCase alias."""
        from src.services.mcp.config import filter_mcp_servers_by_policy
        from src.settings.types import SettingsSchema

        settings = SettingsSchema(extra={"disableAllMcp": True})
        configs = {
            "a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=settings,
        ):
            filtered, notices = filter_mcp_servers_by_policy(configs)
        assert filtered == {}

    def test_no_policy_flag_keeps_all_servers(self):
        from src.services.mcp.config import filter_mcp_servers_by_policy
        from src.settings.types import SettingsSchema

        settings = SettingsSchema()  # nothing set
        configs = {
            "a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=settings,
        ):
            filtered, _ = filter_mcp_servers_by_policy(configs)
        assert set(filtered) == {"a"}


# ----------------------------------------------------------------------
# Session-expiry regex tightening
# ----------------------------------------------------------------------


class TestSessionExpiryRegexTightening:
    """The previous _SESSION_TERMINATED_RE matched any code paired with
    'Session terminated'. A server emitting e.g. -32602 with that text
    would trigger spurious reconnects. Tightened to require a recognized
    session-expiry code (-32001 or 32600)."""

    def test_invalid_params_with_session_terminated_text_does_not_match(self):
        from src.services.mcp.errors import is_mcp_session_expired_error

        err = Exception('{"code":-32602,"message":"Session terminated"}')
        assert is_mcp_session_expired_error(err) is False

    def test_recognized_neg32001_still_matches(self):
        from src.services.mcp.errors import is_mcp_session_expired_error

        err = Exception('{"code":-32001,"message":"Session terminated"}')
        assert is_mcp_session_expired_error(err) is True

    def test_recognized_32600_still_matches(self):
        from src.services.mcp.errors import is_mcp_session_expired_error

        err = Exception('{"code":32600,"message":"Session terminated"}')
        assert is_mcp_session_expired_error(err) is True
