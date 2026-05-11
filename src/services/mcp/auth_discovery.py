"""OAuth metadata discovery: RFC 9728 + RFC 8414 + path-aware fallback.

Phase 4 WI-4.1 (gap #3, blocker). Mirrors the discovery chain in TS
``services/mcp/auth.ts``:
  1. Try ``authServerMetadataUrl`` escape hatch when configured —
     authoritative; failure raises rather than falling through.
  2. RFC 9728 PRM probe; if it returns ``authorization_servers``,
     follow [0] to RFC 8414 AS metadata.
  3. Otherwise fall back to probing RFC 8414 directly against the MCP
     server URL.

Heavy lifting is delegated to the official ``mcp`` PyPI SDK
(``mcp.client.auth.utils``); this module is a thin orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)

from .oauth_redaction import redact_sensitive_params

logger = logging.getLogger(__name__)

_DISCOVERY_TIMEOUT_S = 10.0


class OAuthDiscoveryError(RuntimeError):
    """Raised when no probe in the discovery chain returned valid metadata.

    Includes the URLs attempted (redacted) so operators can verify
    network reachability or set the ``authServerMetadataUrl`` escape
    hatch on the server config.
    """

    def __init__(self, server_url: str, attempted_urls: list[str]):
        super().__init__(
            f"OAuth discovery failed for {server_url}; tried "
            f"{len(attempted_urls)} URL(s) without finding valid metadata. "
            f"Set 'authServerMetadataUrl' on the server config to bypass "
            f"discovery, or verify the server's OAuth advertisement."
        )
        self.server_url = server_url
        # Redact in case any URL carried query-string credentials.
        self.attempted_urls = [redact_sensitive_params(u) for u in attempted_urls]


async def discover_oauth_metadata(
    server_url: str,
    *,
    escape_hatch_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    www_auth_resource_url: str | None = None,
) -> dict[str, Any]:
    """Run the discovery chain and return the AS metadata dict.

    Args:
        server_url: The MCP server's URL — starting point for both the
            RFC 9728 PRM probe and the RFC 8414 fallback.
        escape_hatch_url: Optional ``authServerMetadataUrl`` from the
            server config. When set, fetches AS metadata from this URL
            **authoritatively** — failure raises ``OAuthDiscoveryError``
            instead of falling through to the chain. The escape hatch
            is explicit operator intent.
        http_client: Optional pre-configured httpx client. When None,
            we construct a short-lived one with a 10 s timeout.
        www_auth_resource_url: Optional URL extracted from a prior
            401's ``WWW-Authenticate`` header (RFC 9728 §3.1
            ``resource_metadata`` parameter). Highest priority in the
            PRM probe when provided.

    Returns:
        AS metadata as a dict with required keys ``issuer``,
        ``authorization_endpoint``, ``token_endpoint`` (per RFC 8414).

    Raises:
        OAuthDiscoveryError: when no probe returns valid metadata.
    """
    attempted: list[str] = []
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S)
    try:
        # 1) Escape hatch is authoritative: explicit operator intent.
        if escape_hatch_url:
            # RFC 8414 §2 mandates TLS for AS metadata URLs. The escape
            # hatch can come from a project-scoped .mcp.json — a write
            # surface accessible to untrusted repos — so an attacker
            # could otherwise point the discovery at http:// and steal
            # the eventual access_token from a plaintext channel.
            # Mirrors TS auth.ts:332-334.
            if not escape_hatch_url.lower().startswith("https://"):
                raise OAuthDiscoveryError(server_url, [escape_hatch_url])
            attempted.append(escape_hatch_url)
            metadata = await _try_as_metadata(client, escape_hatch_url)
            if metadata is not None:
                logger.info(
                    "OAuth discovery: used escape-hatch URL for %s",
                    server_url,
                )
                return metadata
            # Explicit URL failed — fail loud rather than silently
            # probing well-known URIs that the operator didn't ask for.
            raise OAuthDiscoveryError(server_url, attempted)

        # 2) RFC 9728 PRM probe.
        prm_urls = build_protected_resource_metadata_discovery_urls(
            www_auth_resource_url, server_url
        )
        for url in prm_urls:
            attempted.append(url)
            authorization_servers = await _try_prm(client, url)
            if not authorization_servers:
                continue
            logger.info(
                "OAuth discovery: PRM hit at %s; %d authorization_server(s) advertised",
                url, len(authorization_servers),
            )
            # 3) Follow authorization_servers[0] to RFC 8414 metadata.
            # AnyUrl → str adds trailing slash; downstream is robust to that.
            as_url = str(authorization_servers[0])
            as_urls = build_oauth_authorization_server_metadata_discovery_urls(
                as_url, server_url
            )
            for as_candidate in as_urls:
                attempted.append(as_candidate)
                metadata = await _try_as_metadata(client, as_candidate)
                if metadata is not None:
                    return metadata

        # 4) Fallback: probe AS metadata directly against the server URL.
        as_urls = build_oauth_authorization_server_metadata_discovery_urls(
            None, server_url
        )
        for as_candidate in as_urls:
            if as_candidate in attempted:
                continue
            attempted.append(as_candidate)
            metadata = await _try_as_metadata(client, as_candidate)
            if metadata is not None:
                logger.info(
                    "OAuth discovery: AS-direct fallback hit at %s",
                    as_candidate,
                )
                return metadata

        raise OAuthDiscoveryError(server_url, attempted)
    finally:
        if own_client:
            await client.aclose()


async def _try_prm(
    client: httpx.AsyncClient, url: str
) -> list[Any] | None:
    """Probe an RFC 9728 PRM URL. Return its ``authorization_servers``
    list on success, None otherwise. The SDK helper returns
    ``ProtectedResourceMetadata | None`` (not a tuple)."""
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        logger.debug("PRM probe %s failed: %s", url, exc)
        return None
    try:
        prm = await handle_protected_resource_response(response)
    except Exception as exc:  # pragma: no cover - SDK-internal parse edges
        logger.debug("PRM probe %s SDK parse failed: %s", url, exc)
        return None
    if prm is None:
        return None
    servers = getattr(prm, "authorization_servers", None)
    return list(servers) if servers else None


async def _try_as_metadata(
    client: httpx.AsyncClient, url: str
) -> dict[str, Any] | None:
    """Probe an RFC 8414 AS-metadata URL. Return the metadata dict on
    success, None otherwise. The SDK helper returns
    ``tuple[bool, OAuthMetadata | None]`` — we treat any non-(_, metadata)
    result as "no usable metadata at this URL"."""
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        logger.debug("AS-metadata probe %s failed: %s", url, exc)
        return None
    try:
        result = await handle_auth_metadata_response(response)
    except Exception as exc:  # pragma: no cover
        logger.debug("AS-metadata probe %s SDK parse failed: %s", url, exc)
        return None
    if not isinstance(result, tuple) or len(result) != 2:
        return None
    _, metadata = result
    if metadata is None:
        return None
    # Pydantic model → dict. ``mode="json"`` serializes ``AnyHttpUrl``
    # as a plain string (default ``model_dump`` returns ``Url`` objects
    # that compare unequal to literal URL strings callers expect).
    return metadata.model_dump(by_alias=True, exclude_none=True, mode="json")
