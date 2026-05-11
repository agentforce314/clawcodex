"""Claude.ai connector loader: surface web-configured MCP servers in the CLI.

Phase 7 WI-7.3 (gap #20). Mirrors typescript/src/services/mcp/
claudeai.ts (174 LOC). Claude.ai subscribers configure MCP connectors
via the web UI; this loader fetches them via the Anthropic API and
surfaces them under the ``claudeai`` config scope so they participate
in the standard merge / dedup pipeline.

Eligibility gates (all must be true to fetch):
  * First-party provider only (env-detected; placeholder check today)
  * ``ENABLE_CLAUDEAI_MCP_SERVERS`` env var is set
  * The CLI's auth token grants the ``user:mcp_servers`` scope

The fetch result is memoized at module-load (a process-lifetime cache);
operators can flush via ``reset_claudeai_cache()`` for testing.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from .types import (
    McpHTTPServerConfig,
    McpSSEServerConfig,
    ScopedMcpServerConfig,
)

logger = logging.getLogger(__name__)

CLAUDEAI_SERVER_NAME_PREFIX = "claude.ai "
_CLAUDEAI_API_URL = "https://api.anthropic.com/v1/mcp_servers"
_CLAUDEAI_BETA_HEADER = "mcp-servers-2025-12-04"
_CLAUDEAI_FETCH_TIMEOUT_S = 5.0
# Memoize for the process lifetime (mirrors TS' module-scope cache).
_cache: dict[str, ScopedMcpServerConfig] | None = None
_cache_at: float = 0.0


def reset_claudeai_cache() -> None:
    """Drop the memoized fetch result. Call from tests + on explicit
    re-fetch requests from the UI."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def get_cached_claudeai_mcp_configs() -> dict[str, ScopedMcpServerConfig]:
    """Return the most recent fetch result without doing I/O.

    Returns ``{}`` if the async ``fetch_claudeai_mcp_configs_if_eligible``
    has not been called yet (or eligibility was denied). This is the
    sync surface that ``config.get_all_mcp_configs`` consumes — the
    boot path is expected to prime the cache via the async helper
    before the sync aggregator runs. When the cache is cold, claudeai
    servers simply don't participate in this merge; the next agent
    boot tick will see them once the prefetch lands.
    """
    return dict(_cache) if _cache else {}


async def fetch_claudeai_mcp_configs_if_eligible(
    *,
    auth_provider: Any | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, ScopedMcpServerConfig]:
    """Return the user's Claude.ai-configured MCP connectors, or ``{}``.

    Returns ``{}`` when:
      * The CLI isn't running against a first-party Anthropic backend.
      * ``ENABLE_CLAUDEAI_MCP_SERVERS`` env is unset.
      * No auth token is available.
      * The HTTP fetch fails or times out (5 s).

    The result is memoized; subsequent calls within the same process
    return the same dict unless ``reset_claudeai_cache()`` is called.
    """
    global _cache, _cache_at
    if _cache is not None:
        return dict(_cache)
    if not _is_claudeai_enabled():
        _cache = {}
        return {}
    token = _resolve_auth_token(auth_provider)
    if not token:
        logger.debug(
            "Claude.ai MCP loader: no first-party auth token; skipping fetch"
        )
        _cache = {}
        return {}
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_CLAUDEAI_FETCH_TIMEOUT_S)
    try:
        try:
            response = await client.get(
                _CLAUDEAI_API_URL,
                params={"limit": 1000},
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": _CLAUDEAI_BETA_HEADER,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("Claude.ai MCP fetch failed: %s", exc)
            _cache = {}
            return {}
        if response.status_code != 200:
            logger.warning(
                "Claude.ai MCP fetch returned HTTP %d; ignoring",
                response.status_code,
            )
            _cache = {}
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Claude.ai MCP fetch returned non-JSON body: %s", exc)
            _cache = {}
            return {}

        servers_raw = payload.get("servers") or payload.get("data") or []
        result: dict[str, ScopedMcpServerConfig] = {}
        for item in servers_raw:
            scoped = _to_scoped_config(item)
            if scoped is not None:
                # Prefix the name so claudeai-sourced servers are visually
                # distinct in the CLI tool list and won't collide with
                # manual entries before the dedup helper runs.
                display_name = item.get("display_name") or item.get("name") or "unnamed"
                key = f"{CLAUDEAI_SERVER_NAME_PREFIX}{display_name}"
                result[key] = scoped
        _cache = result
        _cache_at = time.monotonic()
        return dict(_cache)
    finally:
        if own_client:
            await client.aclose()


def _is_claudeai_enabled() -> bool:
    """Eligibility gate. Two env vars + a first-party check.

    ``ENABLE_CLAUDEAI_MCP_SERVERS=1`` is the explicit operator opt-in.
    ``CLAUDE_PROVIDER`` (when present) must equal ``anthropic`` —
    matches the TS first-party heuristic.
    """
    if os.environ.get("ENABLE_CLAUDEAI_MCP_SERVERS", "").strip() != "1":
        return False
    provider = os.environ.get("CLAUDE_PROVIDER", "anthropic").strip().lower()
    return provider == "anthropic"


def _resolve_auth_token(auth_provider: Any | None) -> str | None:
    """Pull a Claude.ai-side token. Looks first at the optional
    auth_provider's headers (for testing); falls back to the
    ``CLAUDEAI_API_TOKEN`` env var.

    Real first-party deployments would read from ``src/auth/`` (the
    Anthropic OAuth flow); that integration point is left to a follow-up.
    """
    if auth_provider is not None:
        headers = auth_provider.get_auth_headers("__claudeai__")
        if headers:
            auth = headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:]
    return os.environ.get("CLAUDEAI_API_TOKEN") or None


def _to_scoped_config(item: dict[str, Any]) -> ScopedMcpServerConfig | None:
    """Convert one API record into a ``ScopedMcpServerConfig`` of scope
    ``claudeai``. Supports the ``http`` and ``sse`` transports the
    Anthropic API advertises; skips records of unsupported shape."""
    transport = (item.get("transport") or item.get("type") or "").lower()
    url = item.get("url")
    if not url:
        return None
    headers = item.get("headers")
    if transport in ("http", "streamable_http", ""):
        inner = McpHTTPServerConfig(url=url, headers=headers)
    elif transport == "sse":
        inner = McpSSEServerConfig(url=url, headers=headers)
    else:
        logger.debug(
            "Claude.ai MCP loader: skipping unsupported transport %r",
            transport,
        )
        return None
    return ScopedMcpServerConfig(config=inner, scope="claudeai")
