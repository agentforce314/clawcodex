"""Cross-App Access (XAA / SEP-990) token exchange.

Phase 5 WI-5.1 (gap #4, blocker). Mirrors typescript/src/services/mcp/
xaa.ts (511 LOC). Implements the two-step RFC 8693 + RFC 7523 token-
exchange flow used by enterprise IdPs (Okta, Auth0, Azure AD) to grant
an MCP server's access token via an IdP-issued id_token:

  Step 1: POST to the AS with grant_type=token-exchange, subject_token
          = IdP id_token, requested_token_type = id-jag. Receive an
          ID-JAG (Identity Assertion Authorization Grant) token.
  Step 2: POST to the AS with grant_type=jwt-bearer, assertion=ID-JAG.
          Receive the access_token for the MCP server.

References:
  - RFC 8693 (Token Exchange):
    https://datatracker.ietf.org/doc/html/rfc8693
  - RFC 7523 (JWT Bearer):
    https://datatracker.ietf.org/doc/html/rfc7523
  - SEP-990 (Cross-App Access):
    Anthropic SEP / internal spec.
  - RFC 3986 §6.2.2 URL syntax-based normalization.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from .auth import TokenData
from .oauth_error_normalization import normalize_oauth_error_body

logger = logging.getLogger(__name__)


XAA_REQUEST_TIMEOUT_S = 30.0

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"

# Redact tokens from log output. Mirrors TS SENSITIVE_TOKEN_RE.
_SENSITIVE_TOKEN_RE = re.compile(
    r"(?P<key>access_token|id_token|refresh_token|assertion|subject_token|actor_token)"
    r"\"\s*:\s*\"[^\"]+\"",
    re.IGNORECASE,
)


def redact_tokens(payload: str) -> str:
    """Replace OAuth token values in a JSON-shaped string with REDACTED.

    Used for log lines that may include raw JSON bodies.
    """
    return _SENSITIVE_TOKEN_RE.sub(r'\g<key>":"REDACTED"', payload)


def normalize_url(url: str) -> str:
    """RFC 3986 §6.2.2 syntax-based normalization.

    Lower-case the scheme + host; remove the default port; remove a
    single trailing slash from the path. Used to compare AS URLs across
    redirects / aliases.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Drop the default port for the scheme. urlparse exposes ``hostname``
    # and ``port`` for this — netloc lowercase already covers the host.
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    elif scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    path = parsed.path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


@dataclass
class XaaTokenExchangeError(RuntimeError):
    """Raised when the XAA token exchange flow fails at any step.

    ``should_clear_id_token`` flags whether the caller should drop the
    cached IdP id_token (e.g., the IdP rejected the token outright vs.
    a transient backend error).
    """

    message: str
    should_clear_id_token: bool = False

    def __post_init__(self) -> None:
        super().__init__(self.message)


async def perform_cross_app_access(
    *,
    auth_server_url: str,
    id_token: str,
    client_id: str,
    target_audience: str,
    scopes: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenData:
    """Run the two-step XAA flow and return the MCP-server access token.

    Args:
        auth_server_url: The AS token endpoint URL (from OAuth discovery
            metadata).
        id_token: The user's IdP-issued id_token (from xaa_idp_login).
        client_id: Our OAuth client identifier (e.g. ``claude-code-mcp``).
        target_audience: The MCP server URL or resource URI we want the
            ID-JAG / access_token for.
        scopes: Optional list of OAuth scopes to request.
        http_client: Optional pre-configured httpx client (caller owns
            lifecycle when provided).

    Returns:
        ``TokenData`` for the MCP server's access token.

    Raises:
        XaaTokenExchangeError on any step failure. The
        ``should_clear_id_token`` flag indicates whether the IdP
        id_token should be discarded.
    """
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=XAA_REQUEST_TIMEOUT_S)
    try:
        # Step 1: Exchange the id_token for an ID-JAG.
        id_jag = await _exchange_id_token_for_id_jag(
            client,
            auth_server_url=auth_server_url,
            id_token=id_token,
            client_id=client_id,
            target_audience=target_audience,
        )
        # Step 2: Exchange the ID-JAG for an access_token.
        token = await _exchange_id_jag_for_access_token(
            client,
            auth_server_url=auth_server_url,
            id_jag=id_jag,
            client_id=client_id,
            scopes=scopes,
        )
        return token
    finally:
        if own_client:
            await client.aclose()


async def _exchange_id_token_for_id_jag(
    client: httpx.AsyncClient,
    *,
    auth_server_url: str,
    id_token: str,
    client_id: str,
    target_audience: str,
) -> str:
    """Step 1 — RFC 8693 token exchange to obtain an ID-JAG."""
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "client_id": client_id,
        "subject_token": id_token,
        "subject_token_type": ID_TOKEN_TYPE,
        "requested_token_type": ID_JAG_TOKEN_TYPE,
        "audience": target_audience,
    }
    try:
        response = await client.post(
            normalize_url(auth_server_url),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise XaaTokenExchangeError(
            f"XAA token-exchange network error: {exc}",
            should_clear_id_token=False,
        )

    try:
        body = response.json()
    except ValueError:
        body = {}

    status_code, body = normalize_oauth_error_body(response.status_code, body)
    if status_code >= 400:
        # invalid_grant means the IdP id_token is no longer valid;
        # tell the caller to clear it so the user re-authenticates.
        err = body.get("error", "unknown_error") if isinstance(body, dict) else "unknown_error"
        should_clear = err == "invalid_grant"
        raise XaaTokenExchangeError(
            f"XAA token-exchange step 1 failed ({status_code}): {err}",
            should_clear_id_token=should_clear,
        )
    id_jag = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(id_jag, str) or not id_jag:
        raise XaaTokenExchangeError(
            "XAA token-exchange step 1 returned no ID-JAG",
            should_clear_id_token=False,
        )
    return id_jag


async def _exchange_id_jag_for_access_token(
    client: httpx.AsyncClient,
    *,
    auth_server_url: str,
    id_jag: str,
    client_id: str,
    scopes: list[str] | None,
) -> TokenData:
    """Step 2 — RFC 7523 JWT-bearer grant: ID-JAG → access_token."""
    data: dict[str, str] = {
        "grant_type": JWT_BEARER_GRANT,
        "client_id": client_id,
        "assertion": id_jag,
    }
    if scopes:
        data["scope"] = " ".join(scopes)
    try:
        response = await client.post(
            normalize_url(auth_server_url),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise XaaTokenExchangeError(
            f"XAA jwt-bearer network error: {exc}",
            should_clear_id_token=False,
        )

    try:
        body = response.json()
    except ValueError:
        body = {}

    status_code, body = normalize_oauth_error_body(response.status_code, body)
    if status_code >= 400:
        err = body.get("error", "unknown_error") if isinstance(body, dict) else "unknown_error"
        raise XaaTokenExchangeError(
            f"XAA jwt-bearer step 2 failed ({status_code}): {err}",
            should_clear_id_token=False,
        )

    if not isinstance(body, dict):
        raise XaaTokenExchangeError(
            "XAA jwt-bearer step 2 returned non-JSON body",
            should_clear_id_token=False,
        )
    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise XaaTokenExchangeError(
            "XAA jwt-bearer step 2 returned no access_token",
            should_clear_id_token=False,
        )

    expires_at: float | None = None
    if "expires_in" in body:
        try:
            expires_at = time.time() + int(body["expires_in"])
        except (TypeError, ValueError):
            expires_at = None

    return TokenData(
        access_token=access_token,
        token_type=body.get("token_type", "Bearer"),
        refresh_token=body.get("refresh_token"),
        expires_at=expires_at,
        scope=body.get("scope"),
    )
