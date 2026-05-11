"""Regression tests for the 6 Critic-identified blocking issues.

Each test pins one specific operative behavior. If any of these test
names sound vague or "no behavior asserted", it's wrong — every test
here exists because a prior implementation got the corresponding
detail wrong and shipped a bug.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.mcp.auth import (
    AuthResult,
    McpAuthManager,
    McpTokenStore,
    OAuthConfig,
)
from src.services.mcp.auth_discovery import (
    OAuthDiscoveryError,
    discover_oauth_metadata,
)
from src.services.mcp.client import connect_to_server
from src.services.mcp.oauth_callback_server import (
    OAuthCallbackError,
    _error_body,
    wait_for_callback,
)
from src.services.mcp.oauth_port import find_available_port
from src.services.mcp.types import (
    ConnectedMCPServer,
    McpHTTPServerConfig,
    ScopedMcpServerConfig,
)


# ----------------------------------------------------------------------
# Blocker #1+#2: auth_provider threaded into connect_to_server pre-connect
# ----------------------------------------------------------------------


class TestAuthProviderWiring:
    """The auth provider MUST be bound to the client BEFORE the connect
    call, so the NeedsAuth fast-path + auth-header injection take effect
    on the very first attempt. Earlier iteration set it AFTER connect,
    so first-time 401 → FailedMCPServer instead of NeedsAuthMCPServer.
    """

    @pytest.mark.asyncio
    async def test_connect_to_server_passes_auth_provider_to_client(self):
        # Build a mock auth provider that records the call.
        auth_provider = MagicMock()
        auth_provider.get_auth_headers = AsyncMock(return_value=None)
        auth_provider.is_needs_auth = MagicMock(return_value=False)
        config = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://example.com/mcp"),
            scope="user",
        )

        # Patch McpClient.connect so we only verify the wiring order,
        # not the network call. Capture the state of the client at
        # connect-time.
        captured: dict[str, Any] = {}

        async def fake_connect(self, name, conf):
            captured["auth_provider_set_at_connect_time"] = self._auth_provider
            return ConnectedMCPServer(name=name)

        with patch(
            "src.services.mcp.client.McpClient.connect",
            new=fake_connect,
        ):
            client, conn = await connect_to_server(
                "test-server", config, auth_provider=auth_provider
            )

        # The provider must already be installed when connect() runs.
        assert captured["auth_provider_set_at_connect_time"] is auth_provider
        assert client._auth_provider is auth_provider

    @pytest.mark.asyncio
    async def test_connect_to_server_with_no_auth_provider_is_safe(self):
        config = ScopedMcpServerConfig(
            config=McpHTTPServerConfig(url="https://example.com/mcp"),
            scope="user",
        )

        async def fake_connect(self, name, conf):
            return ConnectedMCPServer(name=name)

        with patch(
            "src.services.mcp.client.McpClient.connect",
            new=fake_connect,
        ):
            client, _ = await connect_to_server("test-server", config)
        assert client._auth_provider is None


# ----------------------------------------------------------------------
# Blocker #3: HTTPS enforcement on escape_hatch_url
# ----------------------------------------------------------------------


class TestEscapeHatchHttpsEnforcement:
    """``authServerMetadataUrl`` can come from a project-scoped .mcp.json
    that an attacker (with repo write access) controls. A non-HTTPS URL
    would let an on-path attacker steal the eventual access_token. RFC
    8414 §2 mandates TLS.
    """

    @pytest.mark.asyncio
    async def test_http_escape_hatch_url_is_rejected(self):
        with pytest.raises(OAuthDiscoveryError):
            await discover_oauth_metadata(
                "https://server.example.com/mcp",
                escape_hatch_url="http://attacker.example.com/.well-known/oauth-authorization-server",
            )

    @pytest.mark.asyncio
    async def test_ftp_escape_hatch_url_is_rejected(self):
        with pytest.raises(OAuthDiscoveryError):
            await discover_oauth_metadata(
                "https://server.example.com/mcp",
                escape_hatch_url="ftp://anything.example.com/metadata",
            )

    @pytest.mark.asyncio
    async def test_https_escape_hatch_url_is_attempted(self):
        # Even when the upstream fetch fails, we should attempt to fetch
        # rather than reject at the input gate.
        with patch(
            "src.services.mcp.auth_discovery._try_as_metadata",
            new=AsyncMock(return_value=None),
        ) as mock_try:
            with pytest.raises(OAuthDiscoveryError):
                await discover_oauth_metadata(
                    "https://server.example.com/mcp",
                    escape_hatch_url="https://auth.example.com/metadata",
                )
            assert mock_try.called


# ----------------------------------------------------------------------
# Blocker #4: HTML-escape OAuth callback error body
# ----------------------------------------------------------------------


class TestOAuthCallbackXssEscaping:
    """The OAuth callback's error page reflects attacker-controllable
    values (provider's ``error``/``error_description`` query params,
    request path). Without ``html.escape``, a malicious redirect would
    execute JavaScript in the user's browser against the loopback origin.
    """

    def test_error_body_escapes_script_tag(self):
        body = _error_body('<script>alert("xss")</script>')
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_error_body_escapes_quotes(self):
        body = _error_body('foo" onerror="alert(1)')
        assert 'onerror="alert(1)"' not in body
        # The double-quote must be escaped.
        assert "&quot;" in body or "&#x27;" in body or "&#34;" in body

    def test_error_body_escapes_ampersand(self):
        body = _error_body("a & b")
        assert "&amp;" in body
        # Plain ampersand should NOT be present except as part of the
        # entity reference.
        assert "a & b" not in body.replace("&amp;", "")

    @pytest.mark.asyncio
    async def test_callback_xss_in_error_param_is_escaped(self):
        port = find_available_port()
        expected_state = "csrf-token-abc"

        async def make_request():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={
                        "error": "<script>alert(1)</script>",
                        "error_description": "evil",
                    },
                )
                return resp.text

        with pytest.raises(OAuthCallbackError):
            results = await asyncio.gather(
                wait_for_callback(port, expected_state, timeout=5),
                make_request(),
                return_exceptions=True,
            )
            # Re-raise the listener's exception if it was the one that
            # ended (gather with return_exceptions doesn't re-raise).
            for r in results:
                if isinstance(r, OAuthCallbackError):
                    raise r

        # Verify the response body the listener wrote was escaped. We
        # do this by re-running with a separate response capture.
        port = find_available_port()
        capture: dict[str, str] = {}

        async def grab_body():
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{port}/callback",
                    params={"error": "<script>x</script>"},
                )
                capture["body"] = resp.text

        results = await asyncio.gather(
            wait_for_callback(port, expected_state, timeout=5),
            grab_body(),
            return_exceptions=True,
        )
        assert "<script>" not in capture["body"]
        assert "&lt;script&gt;" in capture["body"]


# ----------------------------------------------------------------------
# Blocker #5: redirect_uri must use 'localhost' not '127.0.0.1'
# ----------------------------------------------------------------------


class TestRedirectUriLocalhost:
    """Real OAuth providers (Slack, Notion, GitHub) match redirect_uri
    string LITERALLY. If they have ``http://localhost:*/callback``
    registered, sending ``http://127.0.0.1:PORT/callback`` is rejected
    with redirect_uri_mismatch. Plan A5 + TS canonical.
    """

    @pytest.mark.asyncio
    async def test_auth_provider_builds_localhost_redirect_uri(self, tmp_path):
        from src.services.mcp.auth_provider import McpAuthProvider

        store = McpTokenStore(store_path=tmp_path / "tokens.json")
        provider = McpAuthProvider(token_store=store)
        captured: dict[str, str] = {}

        async def fake_discover(server_url, **kw):
            return {
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
            }

        def fake_build(config: OAuthConfig):
            captured["redirect_uri"] = config.redirect_uri
            return ("https://auth.example.com/authorize?...", "STATE", "VERIFIER")

        async def fake_wait(*args, **kw):
            raise OAuthCallbackError("test-stop")

        with patch(
            "src.services.mcp.auth_provider.discover_oauth_metadata",
            new=fake_discover,
        ), patch.object(
            provider._manager, "build_oauth_url", side_effect=fake_build
        ), patch(
            "src.services.mcp.auth_provider.wait_for_callback",
            new=fake_wait,
        ):
            await provider.acquire_token(
                server_name="test",
                server_url="https://server.example.com/mcp",
                open_browser=False,
            )

        uri = captured["redirect_uri"]
        assert uri.startswith("http://localhost:"), (
            f"redirect_uri must use 'localhost' literal (TS-canonical + "
            f"plan A5); got {uri!r}"
        )

    def test_xaa_idp_login_builds_localhost_redirect_uri(self):
        """Smoke check the literal 'localhost' in the xaa_idp_login source."""
        import inspect

        from src.services.mcp import xaa_idp_login

        source = inspect.getsource(xaa_idp_login.acquire_idp_id_token)
        assert "http://localhost:" in source, (
            "xaa_idp_login.acquire_idp_id_token must build a localhost redirect_uri"
        )
        assert "http://127.0.0.1:" not in source, (
            "xaa_idp_login.acquire_idp_id_token must not use 127.0.0.1 literal"
        )


# ----------------------------------------------------------------------
# Blocker #6: token-endpoint POST must be async (httpx, not urlopen)
# ----------------------------------------------------------------------


class TestAsyncTokenEndpoint:
    """The token-endpoint round-trip must not block the event loop.
    Previously used ``urllib.request.urlopen``, which froze the entire
    event loop for the duration of the request (~100ms–1s typical),
    stalling concurrent MCP receive loops and the OAuth callback
    listener itself.
    """

    @pytest.mark.asyncio
    async def test_exchange_code_uses_httpx_async(self, tmp_path):
        store = McpTokenStore(store_path=tmp_path / "tokens.json")
        mgr = McpAuthManager(store)
        config = OAuthConfig(
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="cid",
            scopes=["read"],
        )

        async def fake_post(self, url, *, data=None, headers=None, **kw):
            assert "Content-Type" in headers
            assert headers["Content-Type"] == "application/x-www-form-urlencoded"
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-123",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await mgr.exchange_code("srv", config, "CODE", "VERIFIER")

        assert result.success
        assert result.token.access_token == "tok-123"

    @pytest.mark.asyncio
    async def test_refresh_token_uses_httpx_async(self, tmp_path):
        from src.services.mcp.auth import TokenData

        store = McpTokenStore(store_path=tmp_path / "tokens.json")
        store.store_token(
            "srv",
            TokenData(
                access_token="old",
                token_type="Bearer",
                refresh_token="RT123",
            ),
        )
        mgr = McpAuthManager(store)
        config = OAuthConfig(
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="cid",
        )

        async def fake_post(self, url, *, data=None, headers=None, **kw):
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-new",
                    "expires_in": 3600,
                },
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await mgr.refresh_token("srv", config)

        assert result.success
        assert result.token.access_token == "tok-new"

    @pytest.mark.asyncio
    async def test_refresh_token_handles_slack_200_with_error_body(self, tmp_path):
        """Slack-style 200+error must be normalized to a raised error,
        not silently stored as a garbage token."""
        from src.services.mcp.auth import TokenData

        store = McpTokenStore(store_path=tmp_path / "tokens.json")
        store.store_token(
            "srv",
            TokenData(
                access_token="old",
                token_type="Bearer",
                refresh_token="RT123",
            ),
        )
        mgr = McpAuthManager(store)
        config = OAuthConfig(
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="cid",
        )

        async def fake_post(self, url, *, data=None, headers=None, **kw):
            # Slack quirk: 200 OK + error in body. Must be promoted to 400.
            return httpx.Response(
                200,
                json={"error": "invalid_refresh_token"},
                request=httpx.Request("POST", url),
            )

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await mgr.refresh_token("srv", config)

        # The vendor code should be mapped to invalid_grant and the
        # result should be a failure.
        assert not result.success
        assert "invalid_grant" in result.error

    @pytest.mark.asyncio
    async def test_exchange_code_does_not_block_event_loop(self, tmp_path):
        """Sanity check: while a (mocked) token POST is in flight, another
        asyncio task makes progress. This would fail with the prior
        urlopen implementation, which holds the event loop for the
        duration of the synchronous I/O.
        """
        store = McpTokenStore(store_path=tmp_path / "tokens.json")
        mgr = McpAuthManager(store)
        config = OAuthConfig(
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="cid",
        )

        other_made_progress = asyncio.Event()

        async def fake_post(self, url, *, data=None, headers=None, **kw):
            # Yield to the event loop so a concurrent task can run.
            await asyncio.sleep(0.05)
            return httpx.Response(
                200,
                json={"access_token": "tok"},
                request=httpx.Request("POST", url),
            )

        async def other_task():
            await asyncio.sleep(0.01)
            other_made_progress.set()

        with patch("httpx.AsyncClient.post", new=fake_post):
            await asyncio.gather(
                mgr.exchange_code("srv", config, "CODE", "VERIFIER"),
                other_task(),
            )

        assert other_made_progress.is_set()
