"""Tests for Phase 4 WI-4.3 OAuth callback listener + WI-4.5 McpAuthProvider
wiring + WI-4.8 15-min auth-cache TTL + WI-6.2 NeedsAuthMCPServer state.
"""

from __future__ import annotations

import asyncio
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.mcp.auth import McpTokenStore, TokenData
from src.services.mcp.auth_provider import (
    AUTH_CACHE_TTL_S,
    McpAuthProvider,
    is_oauth_required_error,
)
from src.services.mcp.client import McpClient, _is_remote_config
from src.services.mcp.oauth_callback_server import (
    OAuthCallbackError,
    wait_for_callback,
)
from src.services.mcp.oauth_port import find_available_port
from src.services.mcp.types import (
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
    NeedsAuthMCPServer,
    ScopedMcpServerConfig,
)


# ----------------------------------------------------------------------
# WI-4.3 callback server
# ----------------------------------------------------------------------


class TestOAuthCallbackServer:

    @pytest.mark.asyncio
    async def test_successful_callback_returns_code_and_state(self):
        port = find_available_port()
        state = "csrf-token-abc"

        async def make_request():
            # Tiny delay so the server is listening before we connect.
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={"code": "AUTH_CODE_123", "state": state},
                )

        listener_task = asyncio.create_task(
            wait_for_callback(port, state, timeout=5)
        )
        client_task = asyncio.create_task(make_request())
        result = await listener_task
        await client_task
        assert result.code == "AUTH_CODE_123"
        assert result.state == state

    @pytest.mark.asyncio
    async def test_state_mismatch_raises(self):
        port = find_available_port()

        async def make_request():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={"code": "C", "state": "wrong"},
                )

        with pytest.raises(OAuthCallbackError, match="State mismatch"):
            await asyncio.gather(
                wait_for_callback(port, "expected-state", timeout=5),
                make_request(),
            )

    @pytest.mark.asyncio
    async def test_error_param_raises_with_description(self):
        port = find_available_port()

        async def make_request():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={
                        "error": "access_denied",
                        "error_description": "User refused authorization",
                    },
                )

        with pytest.raises(OAuthCallbackError, match="access_denied"):
            await asyncio.gather(
                wait_for_callback(port, "any-state", timeout=5),
                make_request(),
            )

    @pytest.mark.asyncio
    async def test_missing_code_raises(self):
        port = find_available_port()

        async def make_request():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={"state": "s"},  # no code
                )

        with pytest.raises(OAuthCallbackError, match="Missing authorization code"):
            await asyncio.gather(
                wait_for_callback(port, "s", timeout=5),
                make_request(),
            )

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """No client request → wait_for_callback times out cleanly."""
        port = find_available_port()
        with pytest.raises(OAuthCallbackError, match="timed out"):
            await wait_for_callback(port, "s", timeout=0.5)


# ----------------------------------------------------------------------
# WI-4.5 auth_provider — get_auth_headers, mark_needs_auth, TTL cache
# ----------------------------------------------------------------------


class TestAuthProviderHeaderLookup:

    def test_returns_none_when_no_token(self, tmp_path):
        provider = McpAuthProvider(
            token_store=McpTokenStore(store_path=tmp_path / "t.json")
        )
        assert provider.get_auth_headers("srv") is None

    def test_returns_bearer_when_token_present(self, tmp_path):
        store = McpTokenStore(store_path=tmp_path / "t.json")
        store.store_token("srv", TokenData(access_token="abc"))
        provider = McpAuthProvider(token_store=store)
        headers = provider.get_auth_headers("srv")
        assert headers == {"Authorization": "Bearer abc"}

    def test_returns_none_when_token_expired(self, tmp_path):
        store = McpTokenStore(store_path=tmp_path / "t.json")
        store.store_token("srv", TokenData(access_token="abc", expires_at=time.time() - 10))
        provider = McpAuthProvider(token_store=store)
        assert provider.get_auth_headers("srv") is None


class TestNeedsAuthCache:

    def test_mark_and_get_round_trip(self, tmp_path):
        provider = McpAuthProvider(
            token_store=McpTokenStore(store_path=tmp_path / "t.json")
        )
        provider.mark_needs_auth("srv", auth_url="https://x", reason="401")
        entry = provider.get_needs_auth_state("srv")
        assert entry is not None
        assert entry.auth_url == "https://x"
        assert entry.reason == "401"
        assert entry.is_fresh

    def test_clear_drops_entry(self, tmp_path):
        provider = McpAuthProvider(
            token_store=McpTokenStore(store_path=tmp_path / "t.json")
        )
        provider.mark_needs_auth("srv")
        provider.clear_needs_auth("srv")
        assert provider.get_needs_auth_state("srv") is None

    def test_expired_entry_returns_none_and_evicts(self, tmp_path, monkeypatch):
        """A cache entry older than AUTH_CACHE_TTL_S is auto-evicted."""
        provider = McpAuthProvider(
            token_store=McpTokenStore(store_path=tmp_path / "t.json")
        )
        provider.mark_needs_auth("srv")
        # Fast-forward by manipulating the cached_at directly.
        provider._needs_auth_cache["srv"].cached_at = time.time() - (AUTH_CACHE_TTL_S + 10)
        assert provider.get_needs_auth_state("srv") is None
        # And subsequent calls don't see it either (was evicted on access).
        assert "srv" not in provider._needs_auth_cache


class TestIsOauthRequiredError:

    def test_httpx_401_is_oauth_required(self):
        response = httpx.Response(status_code=401, content=b"unauthorized")
        exc = httpx.HTTPStatusError("401", request=MagicMock(), response=response)
        assert is_oauth_required_error(exc) is True

    def test_non_auth_error_returns_false(self):
        exc = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=httpx.Response(500, content=b"")
        )
        assert is_oauth_required_error(exc) is False

    def test_www_authenticate_in_message_matches(self):
        exc = RuntimeError("server rejected: WWW-Authenticate: Bearer")
        assert is_oauth_required_error(exc) is True


# ----------------------------------------------------------------------
# WI-6.2 + WI-4.5 — client.connect produces NeedsAuthMCPServer
# ----------------------------------------------------------------------


class TestClientConnectNeedsAuth:

    @pytest.mark.asyncio
    async def test_remote_config_returns_needs_auth_when_cached(self, tmp_path):
        """When the auth provider's cache says the server needs auth,
        connect() must fast-path to NeedsAuthMCPServer without touching
        the network."""
        store = McpTokenStore(store_path=tmp_path / "t.json")
        provider = McpAuthProvider(token_store=store)
        provider.mark_needs_auth(
            "remote",
            auth_url="https://auth.example/authorize?REDACTED=…",
            reason="prior 401",
        )

        client = McpClient()
        client.set_auth_provider(provider)
        config = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://mcp.example/server"),
            scope="user",
        )
        result = await client.connect("remote", config)
        assert isinstance(result, NeedsAuthMCPServer)
        assert result.auth_method == "oauth"
        assert "auth.example" in (result.auth_url or "")
        assert result.requires_user_action is True

    @pytest.mark.asyncio
    async def test_stdio_config_skips_auth_cache(self, tmp_path):
        """stdio configs never need OAuth — auth provider must NOT be
        consulted, and connect() shouldn't short-circuit via the cache
        path. This guards against false-positive remote-config detection."""
        store = McpTokenStore(store_path=tmp_path / "t.json")
        provider = McpAuthProvider(token_store=store)
        provider.mark_needs_auth("remote", reason="should not matter")

        client = McpClient()
        client.set_auth_provider(provider)
        config = ScopedMcpServerConfig(
            config=McpStdioServerConfig(command="/nonexistent-cmd"),
            scope="user",
        )
        result = await client.connect("remote", config)
        # Should attempt + fail (FileNotFound), not produce NeedsAuth.
        from src.services.mcp.types import FailedMCPServer

        assert isinstance(result, FailedMCPServer)


class TestIsRemoteConfig:

    def test_http_is_remote(self):
        assert _is_remote_config(McpHTTPServerConfig(url="https://x")) is True

    def test_sse_is_remote(self):
        assert _is_remote_config(McpSSEServerConfig(url="https://x")) is True

    def test_ws_is_remote(self):
        assert _is_remote_config(McpWebSocketServerConfig(url="wss://x")) is True

    def test_stdio_is_not_remote(self):
        assert _is_remote_config(McpStdioServerConfig(command="x")) is False
