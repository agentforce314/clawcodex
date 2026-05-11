"""IdP login for Cross-App Access (XAA).

Phase 5 WI-5.2 (gap #4 cont., blocker). Mirrors typescript/src/services/
mcp/xaaIdpLogin.ts (487 LOC). Drives the OIDC browser login against
the configured enterprise IdP (Okta, Auth0, Azure AD, …) and caches the
resulting ``id_token`` for the XAA token-exchange flow (xaa.py) to
consume.

Cache semantics:
  - In-memory only (process lifetime).
  - 60-second safety buffer applied to the JWT ``exp`` claim before
    returning a cached token, so a near-expiry token isn't returned to
    a caller that'll immediately get an "invalid_grant" reply.

The login flow:
  1. OIDC discovery against the IdP issuer (``.well-known/openid-configuration``).
  2. PKCE auth-code flow via the system browser, with our loopback
     callback listener (oauth_callback_server.wait_for_callback).
  3. Token exchange against the IdP's token endpoint → id_token.
  4. Cache.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import webbrowser
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import OAuthConfig, _generate_pkce
from .oauth_callback_server import OAuthCallbackError, wait_for_callback
from .oauth_port import find_available_port

logger = logging.getLogger(__name__)


IDP_LOGIN_TIMEOUT_S = 5 * 60.0  # 5 min total budget for the flow
IDP_REQUEST_TIMEOUT_S = 30.0
ID_TOKEN_EXPIRY_BUFFER_S = 60  # treat tokens as expired 1 min early


@dataclass
class XaaIdpSettings:
    """Minimal IdP configuration shape. Mirrors TS ``XaaIdpSettings``."""

    issuer: str
    client_id: str
    client_secret: str | None = None
    callback_port: int | None = None


@dataclass
class _CachedIdToken:
    id_token: str
    expires_at: float | None  # epoch seconds; None = unknown

    @property
    def is_fresh(self) -> bool:
        if self.expires_at is None:
            # If we can't read exp, treat as fresh (best effort).
            return True
        return time.time() < (self.expires_at - ID_TOKEN_EXPIRY_BUFFER_S)


# Module-state cache: keyed by IdP issuer (normalized).
_id_token_cache: dict[str, _CachedIdToken] = {}


def _issuer_key(issuer: str) -> str:
    """Lowercase + trailing-slash strip so equivalent issuer URLs share a key."""
    norm = issuer.lower()
    if norm.endswith("/"):
        norm = norm[:-1]
    return norm


def is_xaa_enabled() -> bool:
    """Feature gate. Two env vars must align:
      * ``ENABLE_MCP_XAA=1`` — explicit opt-in.
      * ``MCP_XAA_ISSUER`` set — issuer must be configured.
    """
    if os.environ.get("ENABLE_MCP_XAA", "").strip() != "1":
        return False
    return bool(os.environ.get("MCP_XAA_ISSUER", "").strip())


def get_xaa_idp_settings() -> XaaIdpSettings | None:
    """Read IdP config from env. Returns None when XAA is disabled."""
    if not is_xaa_enabled():
        return None
    issuer = os.environ["MCP_XAA_ISSUER"].strip()
    client_id = os.environ.get("MCP_XAA_CLIENT_ID", "").strip()
    if not client_id:
        return None
    client_secret = os.environ.get("MCP_XAA_CLIENT_SECRET") or None
    callback_port_raw = os.environ.get("MCP_XAA_CALLBACK_PORT", "").strip()
    callback_port: int | None = None
    if callback_port_raw.isdigit():
        callback_port = int(callback_port_raw)
    return XaaIdpSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        callback_port=callback_port,
    )


def get_cached_idp_id_token(issuer: str) -> str | None:
    """Return a still-fresh cached id_token for an IdP issuer, or None."""
    key = _issuer_key(issuer)
    entry = _id_token_cache.get(key)
    if entry is None:
        return None
    if not entry.is_fresh:
        _id_token_cache.pop(key, None)
        return None
    return entry.id_token


def save_idp_id_token(issuer: str, id_token: str) -> None:
    """Cache an IdP id_token. Extracts ``exp`` from the JWT for TTL."""
    exp = jwt_exp(id_token)
    _id_token_cache[_issuer_key(issuer)] = _CachedIdToken(
        id_token=id_token, expires_at=exp
    )


def clear_idp_id_token(issuer: str) -> None:
    _id_token_cache.pop(_issuer_key(issuer), None)


def jwt_exp(token: str) -> float | None:
    """Parse a JWT's ``exp`` claim (epoch seconds). Returns None on any
    parse failure — caller treats unknown-exp as "trust the server"."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        # base64url-decode the payload (middle segment).
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        exp = body.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:
        return None
    return None


async def discover_oidc(
    idp_issuer: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch ``{issuer}/.well-known/openid-configuration``. Returns the
    OIDC discovery metadata dict (authorization_endpoint, token_endpoint,
    jwks_uri, …). Raises RuntimeError on HTTP failure."""
    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=IDP_REQUEST_TIMEOUT_S)
    try:
        url = idp_issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            response = await client.get(
                url, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OIDC discovery failed for {idp_issuer}: {exc}")
        if response.status_code != 200:
            raise RuntimeError(
                f"OIDC discovery returned HTTP {response.status_code} for {idp_issuer}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"OIDC discovery returned non-JSON for {idp_issuer}: {exc}")
    finally:
        if own:
            await client.aclose()


async def acquire_idp_id_token(
    settings: XaaIdpSettings | None = None,
    *,
    open_browser: bool = True,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Run the OIDC browser flow and return a fresh id_token.

    Args:
        settings: IdP config; if None, reads from env via
            ``get_xaa_idp_settings()``.
        open_browser: Whether to invoke ``webbrowser.open``. Tests pass
            False.
        http_client: Optional pre-configured httpx client.

    Returns:
        The IdP-issued ``id_token`` (also cached for future calls).

    Raises:
        RuntimeError when XAA is disabled or settings are absent.
        OAuthCallbackError when the browser flow times out or fails.
        RuntimeError when the token-exchange POST fails.
    """
    s = settings or get_xaa_idp_settings()
    if s is None:
        raise RuntimeError(
            "XAA IdP login is disabled or unconfigured "
            "(ENABLE_MCP_XAA / MCP_XAA_ISSUER / MCP_XAA_CLIENT_ID)"
        )

    cached = get_cached_idp_id_token(s.issuer)
    if cached is not None:
        return cached

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=IDP_REQUEST_TIMEOUT_S)
    try:
        metadata = await discover_oidc(s.issuer, http_client=client)
        port = s.callback_port or find_available_port()
        # ``localhost`` (not ``127.0.0.1``) — see auth_provider.py for
        # the full rationale. Plan A5; matches TS canonical.
        redirect_uri = f"http://localhost:{port}/callback"
        config = OAuthConfig(
            authorization_url=str(metadata["authorization_endpoint"]),
            token_url=str(metadata["token_endpoint"]),
            client_id=s.client_id,
            client_secret=s.client_secret,
            scopes=["openid", "profile", "email"],
            redirect_uri=redirect_uri,
            use_pkce=True,
        )
        from .auth import McpAuthManager

        manager = McpAuthManager()
        url, state, verifier = manager.build_oauth_url(config)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception as exc:  # pragma: no cover
                logger.warning("XAA IdP login: webbrowser.open failed: %s", exc)
        try:
            callback = await asyncio.wait_for(
                wait_for_callback(port=port, expected_state=state),
                timeout=IDP_LOGIN_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            raise OAuthCallbackError(
                f"XAA IdP login timed out after {IDP_LOGIN_TIMEOUT_S:.0f}s"
            ) from exc

        # Exchange the auth code for an id_token at the IdP token endpoint.
        data = {
            "grant_type": "authorization_code",
            "code": callback.code,
            "redirect_uri": redirect_uri,
            "client_id": s.client_id,
        }
        if s.client_secret:
            data["client_secret"] = s.client_secret
        if verifier:
            data["code_verifier"] = verifier

        try:
            response = await client.post(
                config.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"XAA IdP token exchange failed: {exc}")
        if response.status_code != 200:
            raise RuntimeError(
                f"XAA IdP token exchange returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"XAA IdP token exchange returned non-JSON: {exc}")
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise RuntimeError("XAA IdP token exchange returned no id_token")
        save_idp_id_token(s.issuer, id_token)
        return id_token
    finally:
        if own:
            await client.aclose()
