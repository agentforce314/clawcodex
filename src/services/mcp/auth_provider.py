"""``McpAuthProvider``: orchestrates OAuth flow for MCP server connections.

Phase 4 WI-4.5 (gap #7, blocker) + WI-4.8 (gap #18). Ties the Phase-4
primitives together:
  - ``McpTokenStore`` (auth.py)       — keyring-backed token storage
  - ``discover_oauth_metadata``       — RFC 9728 / 8414 discovery
  - ``wait_for_callback``             — loopback redirect listener
  - ``find_available_port``           — RFC 8252 §7.3 port allocator
  - ``redact_sensitive_params``       — log-safe URLs

Two public surfaces:

* ``get_auth_headers(server_name)`` — synchronous lookup; returns the
  ``Authorization`` header for an MCP request if a valid token is in the
  store, or None. Cheap; called per request.

* ``acquire_token(server_name, server_url, *, auth_server_metadata_url)``
  — runs the full OAuth-code+PKCE flow: discovery → authorize URL →
  open browser → wait for callback → token exchange → store. Returns
  ``AuthResult``. Slow; called only when ``get_auth_headers`` returns
  None and the caller has decided the server needs auth.

Phase 4 WI-4.8 (15-min auth-cache TTL): once a server is determined to
need auth and the user hasn't completed the flow yet, we cache that
state for 15 minutes so concurrent attempts to connect don't all
independently retrigger OAuth discovery / browser open.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import webbrowser
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import (
    AuthResult,
    McpAuthManager,
    McpTokenStore,
    OAuthConfig,
    TokenData,
)
from .auth_discovery import (
    OAuthDiscoveryError,
    discover_oauth_metadata,
)
from .oauth_callback_server import (
    OAuthCallbackError,
    wait_for_callback,
)
from .oauth_error_normalization import normalize_oauth_error_body
from .oauth_port import find_available_port
from .oauth_redaction import redact_sensitive_params

logger = logging.getLogger(__name__)


# WI-4.8: cache "this server needs auth" verdicts for 15 minutes so N
# concurrent callers don't each independently retrigger the OAuth flow
# / discovery against the same expired token. Mirrors TS' MCP_AUTH_
# CACHE_TTL_MS = 15 * 60 * 1000.
AUTH_CACHE_TTL_S = 15 * 60


@dataclass
class _NeedsAuthCacheEntry:
    """Records that a server was last observed to need OAuth, with the
    auth URL captured (if available) so the runtime manager can present
    it to the user."""

    cached_at: float
    auth_url: str | None
    reason: str

    @property
    def is_fresh(self) -> bool:
        return time.time() - self.cached_at < AUTH_CACHE_TTL_S


class McpAuthProvider:
    """Wires OAuth-discovery, token-storage, callback-listening, and
    token-exchange into a single connect-time integration point."""

    def __init__(
        self,
        token_store: McpTokenStore | None = None,
        auth_manager: McpAuthManager | None = None,
        client_id: str = "claude-code-mcp",
        scopes: tuple[str, ...] = (),
    ) -> None:
        self._store = token_store or McpTokenStore()
        self._manager = auth_manager or McpAuthManager(token_store=self._store)
        self._client_id = client_id
        self._scopes = list(scopes)
        # WI-4.8: 15-min needs-auth cache.
        self._needs_auth_cache: dict[str, _NeedsAuthCacheEntry] = {}
        # Serialize concurrent acquire_token attempts for the same server.
        self._inflight_locks: dict[str, asyncio.Lock] = {}

    # --- Read path -------------------------------------------------------

    def get_auth_headers(self, server_name: str) -> dict[str, str] | None:
        """Return the ``Authorization`` header for a request, or None if
        no valid token is available. Cheap, non-blocking."""
        token = self._store.get_token(server_name)
        if token is None or token.is_expired:
            return None
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    def get_needs_auth_state(self, server_name: str) -> _NeedsAuthCacheEntry | None:
        """Return the cached needs-auth state for a server if still fresh,
        else None. WI-4.8 implementation."""
        entry = self._needs_auth_cache.get(server_name)
        if entry is None:
            return None
        if not entry.is_fresh:
            self._needs_auth_cache.pop(server_name, None)
            return None
        return entry

    def mark_needs_auth(
        self,
        server_name: str,
        *,
        auth_url: str | None = None,
        reason: str = "OAuth required",
    ) -> None:
        """Record that this server needs auth. Cached for 15 min."""
        self._needs_auth_cache[server_name] = _NeedsAuthCacheEntry(
            cached_at=time.time(),
            auth_url=auth_url,
            reason=reason,
        )

    def clear_needs_auth(self, server_name: str) -> None:
        """Drop the cached needs-auth state (e.g. after successful auth)."""
        self._needs_auth_cache.pop(server_name, None)

    # --- Write path ------------------------------------------------------

    async def acquire_token(
        self,
        server_name: str,
        server_url: str,
        *,
        auth_server_metadata_url: str | None = None,
        open_browser: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> AuthResult:
        """Run the full OAuth-code+PKCE flow end-to-end.

        Returns ``AuthResult(success=True, token=...)`` on success;
        ``AuthResult(success=False, error=...)`` on failure (discovery
        failed, callback timed out, token exchange failed, etc.). The
        method is serialized per server_name so concurrent callers all
        observe the same outcome.
        """
        # ``setdefault`` always evaluates its default arg, so this would
        # build a discarded ``asyncio.Lock()`` on every call when a lock
        # already exists. Cheap but wasteful; guard with a membership
        # check.
        lock = self._inflight_locks.get(server_name)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight_locks[server_name] = lock
        async with lock:
            # Double-check: another caller may have completed while we waited.
            existing = self._store.get_token(server_name)
            if existing is not None and not existing.is_expired:
                self.clear_needs_auth(server_name)
                return AuthResult(success=True, token=existing)

            try:
                metadata = await discover_oauth_metadata(
                    server_url,
                    escape_hatch_url=auth_server_metadata_url,
                    http_client=http_client,
                )
            except OAuthDiscoveryError as exc:
                self.mark_needs_auth(server_name, reason=str(exc))
                return AuthResult(success=False, error=str(exc))

            authorization_endpoint = metadata.get("authorization_endpoint")
            token_endpoint = metadata.get("token_endpoint")
            if not authorization_endpoint or not token_endpoint:
                msg = (
                    "OAuth metadata missing authorization_endpoint or "
                    "token_endpoint"
                )
                self.mark_needs_auth(server_name, reason=msg)
                return AuthResult(success=False, error=msg)

            port = find_available_port()
            # Use ``localhost`` (not ``127.0.0.1``) to match TS canonical
            # (oauthPort.ts) and the way real OAuth providers register
            # redirect URIs. Providers match the redirect_uri string
            # *literally*; if Slack/Notion/GitHub have ``http://localhost:*/callback``
            # on file, sending ``http://127.0.0.1:PORT/callback`` is
            # rejected with redirect_uri_mismatch. The callback listener
            # binds 127.0.0.1 (RFC 8252 §7.3 anti-DNS-rebinding); the
            # OS resolves ``localhost`` to it. Plan assumption A5.
            redirect_uri = f"http://localhost:{port}/callback"
            # Scope selection: when the caller named explicit scopes, use
            # them. Otherwise leave empty — the AS picks its minimum
            # default. Requesting every value in ``scopes_supported`` is
            # an overreach (ASs publish their full catalog; many ASs
            # reject the request when an unprivileged client asks for
            # admin-tier scopes). TS computes a specific scope set
            # per-server-type; the safe default here is "ask for nothing
            # explicit; let the AS pick."
            config = OAuthConfig(
                authorization_url=str(authorization_endpoint),
                token_url=str(token_endpoint),
                client_id=self._client_id,
                redirect_uri=redirect_uri,
                scopes=list(self._scopes),
                use_pkce=True,
            )

            url, state, verifier = self._manager.build_oauth_url(config)
            self.mark_needs_auth(
                server_name,
                auth_url=redact_sensitive_params(url),
                reason="Browser authorization required",
            )

            logger.info(
                "MCP OAuth: opening browser for %r (url=%s)",
                server_name, redact_sensitive_params(url),
            )
            if open_browser:
                try:
                    webbrowser.open(url)
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "MCP OAuth: webbrowser.open failed for %r: %s",
                        server_name, exc,
                    )

            try:
                callback = await wait_for_callback(
                    port=port, expected_state=state
                )
            except OAuthCallbackError as exc:
                msg = f"OAuth callback failed: {exc}"
                self.mark_needs_auth(server_name, reason=msg)
                return AuthResult(success=False, error=msg)

            result = await self._manager.exchange_code(
                server_name=server_name,
                config=config,
                code=callback.code,
                verifier=verifier,
            )
            if result.success:
                self.clear_needs_auth(server_name)
            return result


def is_oauth_required_error(exc: Exception) -> bool:
    """Heuristic: detect transport-level signals that an OAuth flow is
    needed (HTTP 401, ``WWW-Authenticate`` mentioning Bearer, etc.).

    Mirrors TS' ``wrapFetchWithStepUpDetection`` predicate. Permissive
    by design — we only use this to *probe* whether to acquire a token,
    not to make security decisions.
    """
    # HTTP-status path (httpx-shaped errors).
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == 401:
        return True
    msg = str(exc).lower()
    if "www-authenticate" in msg or "unauthorized" in msg:
        return True
    return False
