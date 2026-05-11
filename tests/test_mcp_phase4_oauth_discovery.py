"""Tests for Phase 4 WI-4.1 OAuth discovery wrapper."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.services.mcp.auth_discovery import (
    OAuthDiscoveryError,
    discover_oauth_metadata,
)


def _make_response(status: int, body: dict | str) -> httpx.Response:
    """Build a real httpx.Response so SDK response handlers work."""
    if isinstance(body, dict):
        content = json.dumps(body).encode("utf-8")
        headers = {"content-type": "application/json"}
    else:
        content = body.encode("utf-8")
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code=status, content=content, headers=headers)


_VALID_AS_METADATA = {
    "issuer": "https://auth.example.com",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/token",
    "response_types_supported": ["code"],
}

_VALID_PRM = {
    "resource": "https://mcp.example.com/server",
    "authorization_servers": ["https://auth.example.com"],
}


class TestDiscoverOauthMetadata:

    @pytest.mark.asyncio
    async def test_escape_hatch_short_circuits_chain(self):
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(200, _VALID_AS_METADATA))
        result = await discover_oauth_metadata(
            "https://mcp.example.com/server",
            escape_hatch_url="https://auth.example.com/.well-known/oauth-authorization-server",
            http_client=client,
        )
        # pydantic AnyHttpUrl serializes with trailing slash via mode="json".
        assert result["issuer"].rstrip("/") == "https://auth.example.com"
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_escape_hatch_failure_raises_immediately(self):
        """Explicit escape_hatch_url is authoritative — failure must
        raise rather than silently fall through to discovery."""
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(404, {}))
        with pytest.raises(OAuthDiscoveryError):
            await discover_oauth_metadata(
                "https://mcp.example.com/server",
                escape_hatch_url="https://nowhere.example/.well-known/oauth-authorization-server",
                http_client=client,
            )
        # Only the escape hatch was tried.
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_prm_path_finds_then_fetches_as_metadata(self):
        prm_response = _make_response(200, _VALID_PRM)
        as_response = _make_response(200, _VALID_AS_METADATA)
        client = MagicMock()
        client.get = AsyncMock(side_effect=[prm_response, as_response])
        result = await discover_oauth_metadata(
            "https://mcp.example.com/server",
            http_client=client,
        )
        assert "authorize" in result["authorization_endpoint"]

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_as_probe_when_prm_fails(self):
        """If every PRM URL returns 404, fall back to AS-direct probe."""
        prm_404 = _make_response(404, {"error": "not found"})
        as_response = _make_response(200, _VALID_AS_METADATA)
        client = MagicMock()
        # SDK builds 2 PRM URLs + 1 AS-direct fallback URL for our server URL.
        client.get = AsyncMock(side_effect=[prm_404, prm_404, as_response])
        result = await discover_oauth_metadata(
            "https://mcp.example.com/server",
            http_client=client,
        )
        assert result["issuer"].rstrip("/") == "https://auth.example.com"

    @pytest.mark.asyncio
    async def test_raises_when_all_probes_fail(self):
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(404, {}))
        with pytest.raises(OAuthDiscoveryError) as excinfo:
            await discover_oauth_metadata(
                "https://mcp.example.com/server",
                http_client=client,
            )
        assert len(excinfo.value.attempted_urls) > 0
        assert excinfo.value.server_url == "https://mcp.example.com/server"
        assert "authServerMetadataUrl" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_network_errors_treated_as_probe_failure(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("network unreachable"))
        with pytest.raises(OAuthDiscoveryError):
            await discover_oauth_metadata(
                "https://mcp.example.com/server",
                http_client=client,
            )

    @pytest.mark.asyncio
    async def test_uses_caller_provided_client(self):
        """When http_client is provided, the function MUST NOT close it."""
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(200, _VALID_AS_METADATA))
        client.aclose = AsyncMock()
        await discover_oauth_metadata(
            "https://mcp.example.com/server",
            escape_hatch_url="https://auth.example.com/.well-known/oauth-authorization-server",
            http_client=client,
        )
        client.aclose.assert_not_called()
