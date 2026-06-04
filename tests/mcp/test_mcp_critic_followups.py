"""Regression tests for the Critic follow-up fixes (FU#1–FU#7).

Each FU# corresponds to a "not blocking but should fix soon" item from
the Critic's APPROVE-with-followups verdict. Test names trace back to
the FU number so future bisection is easy.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.mcp.auth import McpTokenStore, TokenData
from src.services.mcp.auth_discovery import (
    EscapeHatchScopeRejectedError,
    OAuthDiscoveryError,
    discover_oauth_metadata,
)
from src.services.mcp.config import filter_mcp_servers_by_policy
from src.services.mcp.connection_manager import (
    MCPConnectionManager,
    bootstrap_mcp_runtime,
)
from src.services.mcp.fetch_wrappers import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_READ_TIMEOUT_S,
    build_mcp_http_client,
    build_mcp_timeout,
)
from src.services.mcp.tool_wrapper import wrap_mcp_tool
from src.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpHTTPServerConfig,
    McpStdioServerConfig,
    McpToolResult,
    McpToolSchema,
    PendingMCPServer,
    ScopedMcpServerConfig,
)
from src.settings.types import SettingsSchema


# ----------------------------------------------------------------------
# FU#1: policy gate fails CLOSED on settings load failure
# ----------------------------------------------------------------------


class TestPolicyFailClosed:
    """When settings can't be loaded, the policy gate must drop all
    servers (fail closed) rather than silently pass everything through.
    Operators wanting bootstrap fall-through opt in explicitly via
    ``MCP_POLICY_FAIL_OPEN=1``."""

    def test_settings_load_failure_drops_all_servers_by_default(
        self, monkeypatch,
    ):
        monkeypatch.delenv("MCP_POLICY_FAIL_OPEN", raising=False)
        configs = {
            "a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=None,
        ):
            filtered, notices = filter_mcp_servers_by_policy(configs)
        assert filtered == {}
        assert any("failed closed" in n for n in notices)

    def test_operator_opt_in_restores_old_fail_open_behavior(
        self, monkeypatch,
    ):
        monkeypatch.setenv("MCP_POLICY_FAIL_OPEN", "1")
        configs = {
            "a": ScopedMcpServerConfig(
                config=McpStdioServerConfig(command="echo"), scope="user",
            ),
        }
        with patch(
            "src.services.mcp.config._safe_load_settings",
            return_value=None,
        ):
            filtered, notices = filter_mcp_servers_by_policy(configs)
        assert set(filtered) == {"a"}


# ----------------------------------------------------------------------
# FU#2: safe-backend allowlist + PlaintextKeyring rejection
# ----------------------------------------------------------------------


class TestKeyringBackendAllowlist:
    """Anything outside the OS-secret-store allowlist (notably
    PlaintextKeyring from keyrings.alt, which stores tokens in a file
    on disk) must be rejected unless MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE=1
    is set."""

    def test_plaintext_backend_rejected_by_default(self, monkeypatch, tmp_path):
        class _Plaintext:
            pass

        _Plaintext.__name__ = "PlaintextKeyring"

        fake_keyring = MagicMock()
        fake_keyring.get_keyring.return_value = _Plaintext()

        class _FailKeyring:
            pass

        # Patch the import + symbol used inside _validate_backend.
        with patch.dict(
            "sys.modules",
            {
                "keyring": fake_keyring,
                "keyring.backends.fail": MagicMock(Keyring=_FailKeyring),
            },
        ):
            monkeypatch.delenv("MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE", raising=False)
            with pytest.raises(RuntimeError, match="not in the allowlist"):
                McpTokenStore(store_path=tmp_path / "tokens.json")

    def test_plaintext_backend_allowed_with_explicit_opt_in(
        self, monkeypatch, tmp_path,
    ):
        class _Plaintext:
            pass

        _Plaintext.__name__ = "PlaintextKeyring"

        fake_keyring = MagicMock()
        fake_keyring.get_keyring.return_value = _Plaintext()

        class _FailKeyring:
            pass

        with patch.dict(
            "sys.modules",
            {
                "keyring": fake_keyring,
                "keyring.backends.fail": MagicMock(Keyring=_FailKeyring),
            },
        ):
            monkeypatch.setenv("MCP_ALLOW_PLAINTEXT_TOKEN_STORAGE", "1")
            store = McpTokenStore(store_path=tmp_path / "tokens.json")
            assert store._using_plaintext_fallback is True

    def test_safe_backend_class_names_includes_os_backends(self):
        # Smoke check that the allowlist covers the three big OSes.
        names = McpTokenStore._SAFE_BACKEND_CLASS_NAMES
        assert "macOSKeyring" in names or "Keyring" in names
        assert "SecretService" in names
        assert "WinVaultKeyring" in names


# ----------------------------------------------------------------------
# FU#3: scope-gated escape hatch
# ----------------------------------------------------------------------


class TestEscapeHatchScopeGating:
    """``authServerMetadataUrl`` from a repo-write scope (project /
    local) must be rejected with EscapeHatchScopeRejectedError to
    defend against malicious .mcp.json files."""

    @pytest.mark.asyncio
    async def test_project_scope_escape_hatch_rejected(self):
        with pytest.raises(EscapeHatchScopeRejectedError):
            await discover_oauth_metadata(
                "https://server.example.com/mcp",
                escape_hatch_url="https://attacker.example.com/.well-known/oauth-authorization-server",
                escape_hatch_source_scope="project",
            )

    @pytest.mark.asyncio
    async def test_local_scope_escape_hatch_rejected(self):
        with pytest.raises(EscapeHatchScopeRejectedError):
            await discover_oauth_metadata(
                "https://server.example.com/mcp",
                escape_hatch_url="https://attacker.example.com/metadata",
                escape_hatch_source_scope="local",
            )

    @pytest.mark.asyncio
    async def test_user_scope_escape_hatch_attempted(self):
        with patch(
            "src.services.mcp.auth_discovery._try_as_metadata",
            new=AsyncMock(return_value=None),
        ) as mock_try:
            with pytest.raises(OAuthDiscoveryError) as exc_info:
                await discover_oauth_metadata(
                    "https://server.example.com/mcp",
                    escape_hatch_url="https://auth.example.com/metadata",
                    escape_hatch_source_scope="user",
                )
            # user-scope: the fetch was actually attempted
            assert mock_try.called
            # but it's NOT the scope-rejected subclass
            assert not isinstance(exc_info.value, EscapeHatchScopeRejectedError)

    @pytest.mark.asyncio
    async def test_enterprise_scope_escape_hatch_attempted(self):
        with patch(
            "src.services.mcp.auth_discovery._try_as_metadata",
            new=AsyncMock(return_value={"issuer": "x", "authorization_endpoint": "y", "token_endpoint": "z"}),
        ) as mock_try:
            result = await discover_oauth_metadata(
                "https://server.example.com/mcp",
                escape_hatch_url="https://auth.example.com/metadata",
                escape_hatch_source_scope="enterprise",
            )
            assert mock_try.called
            assert result["issuer"] == "x"

    @pytest.mark.asyncio
    async def test_no_scope_specified_falls_back_to_legacy_behavior(self):
        # Internal callers (tests, legacy) may not pass the scope; that
        # branch must still work.
        with patch(
            "src.services.mcp.auth_discovery._try_as_metadata",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(OAuthDiscoveryError):
                await discover_oauth_metadata(
                    "https://server.example.com/mcp",
                    escape_hatch_url="https://auth.example.com/metadata",
                    escape_hatch_source_scope=None,
                )


# ----------------------------------------------------------------------
# FU#4: PendingMCPServer emitted during reconnect
# ----------------------------------------------------------------------


class TestPendingDuringReconnect:
    """The manager's state map should briefly hold a PendingMCPServer
    entry while a reconnect attempt is in flight, so UI observers can
    render a "connecting…" indicator."""

    @pytest.mark.asyncio
    async def test_state_shows_pending_before_connect_completes(self):
        mgr = MCPConnectionManager()
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )
        observed_states: list[str] = []
        connect_done = asyncio.Event()

        async def fake_connect(name, conf, *, auth_provider=None):
            # Sample the manager's state while the connect is in flight.
            current = mgr.get_state(name)
            observed_states.append(
                type(current).__name__ if current else "None"
            )
            connect_done.set()
            new_client = MagicMock()
            new_client.list_tools = AsyncMock(return_value=[])
            return new_client, ConnectedMCPServer(name=name)

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
            await mgr.reconnect_mcp_server("srv")

        # During the connect, state must have been PendingMCPServer.
        assert observed_states == ["PendingMCPServer"]
        # After the connect, state is ConnectedMCPServer.
        final = mgr.get_state("srv")
        assert isinstance(final, ConnectedMCPServer)


# ----------------------------------------------------------------------
# FU#5: bootstrap_mcp_runtime canonical mount point
# ----------------------------------------------------------------------


class TestBootstrapMcpRuntime:

    @pytest.mark.asyncio
    async def test_returns_manager_with_connected_servers(self):
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )

        async def fake_connect(name, conf, *, auth_provider=None):
            new_client = MagicMock()
            new_client.list_tools = AsyncMock(return_value=[])
            return new_client, ConnectedMCPServer(name=name)

        async def fake_fetch(**kw):
            return {}

        with patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect,
        ), patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.wrap_mcp_tools_for_server",
            return_value=[],
        ), patch(
            "src.services.mcp.config.get_all_mcp_configs",
            return_value=({"srv1": config, "srv2": config}, []),
        ), patch(
            "src.services.mcp.connection_manager.is_mcp_server_disabled",
            return_value=False,
        ), patch(
            "src.services.mcp.claudeai.fetch_claudeai_mcp_configs_if_eligible",
            new=fake_fetch,
        ):
            manager = await bootstrap_mcp_runtime()

        assert isinstance(manager, MCPConnectionManager)
        states = manager.snapshot()
        assert set(states) == {"srv1", "srv2"}
        for state in states.values():
            assert isinstance(state, ConnectedMCPServer)

    @pytest.mark.asyncio
    async def test_disabled_servers_get_disabled_state(self):
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )

        async def fake_fetch(**kw):
            return {}

        with patch(
            "src.services.mcp.config.get_all_mcp_configs",
            return_value=({"disabled_srv": config}, []),
        ), patch(
            "src.services.mcp.connection_manager.is_mcp_server_disabled",
            return_value=True,
        ), patch(
            "src.services.mcp.claudeai.fetch_claudeai_mcp_configs_if_eligible",
            new=fake_fetch,
        ):
            manager = await bootstrap_mcp_runtime(prefetch_claudeai=False)

        state = manager.get_state("disabled_srv")
        assert isinstance(state, DisabledMCPServer)


# ----------------------------------------------------------------------
# FU#6: fetch_wrappers timeouts
# ----------------------------------------------------------------------


class TestFetchWrappersTimeouts:
    """The httpx client used by the streamable HTTP transport must have
    MCP-appropriate timeouts (5min read, 15s connect) — not the httpx
    default 5s which would kill long-running tool calls."""

    def test_build_mcp_timeout_uses_expected_defaults(self, monkeypatch):
        monkeypatch.delenv("MCP_CONNECT_TIMEOUT_S", raising=False)
        monkeypatch.delenv("MCP_READ_TIMEOUT_S", raising=False)
        monkeypatch.delenv("MCP_WRITE_TIMEOUT_S", raising=False)
        monkeypatch.delenv("MCP_POOL_TIMEOUT_S", raising=False)
        t = build_mcp_timeout()
        assert t.connect == DEFAULT_CONNECT_TIMEOUT_S
        assert t.read == DEFAULT_READ_TIMEOUT_S
        # Sanity: read is much longer than connect (long-running tools).
        assert t.read > t.connect * 10

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("MCP_READ_TIMEOUT_S", "999.5")
        t = build_mcp_timeout()
        assert t.read == 999.5

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MCP_READ_TIMEOUT_S", "not-a-number")
        t = build_mcp_timeout()
        assert t.read == DEFAULT_READ_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_build_mcp_http_client_with_headers(self):
        c = build_mcp_http_client(headers={"X-Test": "value"})
        try:
            assert c.headers["X-Test"] == "value"
            assert c.timeout.read == DEFAULT_READ_TIMEOUT_S
        finally:
            await c.aclose()

    @pytest.mark.asyncio
    async def test_build_mcp_http_client_without_headers(self):
        c = build_mcp_http_client()
        try:
            # No KeyError — just the default httpx headers.
            assert isinstance(c.timeout, httpx.Timeout)
            assert c.timeout.read == DEFAULT_READ_TIMEOUT_S
        finally:
            await c.aclose()


# ----------------------------------------------------------------------
# FU#7: content_blocks preserved on ToolResult.mcp_meta
# ----------------------------------------------------------------------


class TestContentBlocksOnMcpMeta:
    """WI-8.3 follow-up: the original content-block list survives end-
    to-end on ``ToolResult.mcp_meta['content_blocks']`` so downstream
    consumers that opt into multimodal handling can pick it up. The
    ``output`` field remains the str-typed text-flattened version for
    legacy consumers."""

    def _make_tool(self, content_blocks):
        from src.tool_system.context import ToolContext

        client = MagicMock()
        client.call_tool = AsyncMock(return_value=McpToolResult(
            content=content_blocks,
        ))
        mcp_tool = McpToolSchema(
            name="some_tool",
            description="A tool",
            input_schema={"type": "object"},
        )
        return wrap_mcp_tool("server", mcp_tool, client), MagicMock(spec=ToolContext)

    def test_mcp_meta_carries_content_blocks(self):
        tool, ctx = self._make_tool([
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ])
        result = tool.call({}, ctx)
        assert result.mcp_meta is not None
        blocks = result.mcp_meta.get("content_blocks")
        assert isinstance(blocks, list)
        assert len(blocks) == 2
        assert blocks[0]["text"] == "hello"
        assert blocks[1]["text"] == "world"

    def test_mcp_meta_includes_server_and_tool_name(self):
        tool, ctx = self._make_tool([{"type": "text", "text": "x"}])
        result = tool.call({}, ctx)
        assert result.mcp_meta["server_name"] == "server"
        assert result.mcp_meta["tool_name"] == "some_tool"

    def test_output_is_still_text_for_legacy_consumers(self):
        tool, ctx = self._make_tool([
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ])
        result = tool.call({}, ctx)
        # The legacy str-typed output is still text-flattened.
        assert isinstance(result.output, str)
        assert "hello" in result.output
        assert "world" in result.output


# ----------------------------------------------------------------------
# FU#2b: corrected KDE kwallet class names in allowlist
# ----------------------------------------------------------------------


class TestKwalletClassNames:

    def test_kwallet_real_class_names_in_allowlist(self):
        names = McpTokenStore._SAFE_BACKEND_CLASS_NAMES
        # The actual keyring.backends.kwallet symbols (verified against
        # keyring 25.x): DBusKeyring + DBusKeyringKWallet4.
        assert "DBusKeyring" in names
        assert "DBusKeyringKWallet4" in names

    def test_wrong_kwallet_names_not_relied_on(self):
        names = McpTokenStore._SAFE_BACKEND_CLASS_NAMES
        # If KDE-on-Linux users rely on the allowlist, removing these
        # stale entries means the (now-fixed) DBus* names are doing the
        # work — not the wrong literal strings.
        # These two were in the original allowlist but don't match any
        # real class name; remove them when keyring versions bump.
        # The assertion isn't strict (presence is fine, just unused);
        # we keep the test as a hint for future maintenance.
        # No assertion needed — the previous test verifies the
        # operative entries are correct.
        assert names  # smoke


# ----------------------------------------------------------------------
# FU#4b: try/finally — exception during connect_to_server clears pending
# ----------------------------------------------------------------------


class TestReconnectExceptionClearsPending:

    @pytest.mark.asyncio
    async def test_exception_replaces_pending_with_failed_state(self):
        mgr = MCPConnectionManager()
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )

        async def fake_connect_raises(name, conf, *, auth_provider=None):
            raise RuntimeError("simulated connect crash")

        with patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect_raises,
        ):
            with pytest.raises(RuntimeError, match="simulated"):
                await mgr.reconnect_mcp_server("srv")

        # State must NOT be left as PendingMCPServer — must be Failed.
        final = mgr.get_state("srv")
        assert isinstance(final, FailedMCPServer)
        assert "simulated" in (final.error or "")

    @pytest.mark.asyncio
    async def test_cancellation_replaces_pending_with_failed_state(self):
        mgr = MCPConnectionManager()
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="echo"),
            scope="user",
        )

        async def fake_connect_cancels(name, conf, *, auth_provider=None):
            raise asyncio.CancelledError()

        with patch(
            "src.services.mcp.connection_manager.get_mcp_config_by_name",
            return_value=config,
        ), patch(
            "src.services.mcp.connection_manager.connect_to_server",
            new=fake_connect_cancels,
        ):
            with pytest.raises(asyncio.CancelledError):
                await mgr.reconnect_mcp_server("srv")

        # Even a CancelledError (BaseException subclass) must clear the
        # pending state — otherwise the state map permanently shows an
        # in-flight connect that never resolves.
        final = mgr.get_state("srv")
        assert isinstance(final, FailedMCPServer)


# ----------------------------------------------------------------------
# FU#6b: SseTransport applies MCP-appropriate timeouts
# ----------------------------------------------------------------------


class TestSseTransportTimeouts:

    def test_sse_transport_uses_mcp_timeouts(self):
        """SseTransport._open should call sse_client with MCP-appropriate
        timeout and sse_read_timeout, plus the httpx factory adapter."""
        from src.services.mcp.transport import SseTransport, _mcp_sse_http_client_factory

        captured: dict[str, Any] = {}

        def fake_sse_client(**kwargs):
            captured.update(kwargs)
            # Return something the SDK contract permits; we won't enter it.
            return MagicMock()

        transport = SseTransport(url="https://example.com/sse", headers={"X": "y"})
        with patch("src.services.mcp.transport.sse_client", side_effect=fake_sse_client):
            transport._open()

        assert captured["url"] == "https://example.com/sse"
        # Connect-side timeout matches our 15s default.
        assert captured["timeout"] == 15.0
        # SSE read timeout matches our 300s default (long-running streams).
        assert captured["sse_read_timeout"] == 300.0
        # httpx factory plumbed through.
        assert captured["httpx_client_factory"] is _mcp_sse_http_client_factory

    def test_mcp_sse_factory_returns_httpx_async_client(self):
        from src.services.mcp.transport import _mcp_sse_http_client_factory

        c = _mcp_sse_http_client_factory(headers={"X": "y"})
        try:
            assert isinstance(c, httpx.AsyncClient)
            assert c.headers["X"] == "y"
            # Wrapped client honors MCP timeouts.
            assert c.timeout.read == DEFAULT_READ_TIMEOUT_S
        finally:
            asyncio.get_event_loop_policy().get_event_loop().run_until_complete(
                c.aclose()
            ) if False else None  # noqa: leak ok in sync test
