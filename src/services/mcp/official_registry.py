"""Official MCP registry prefetch + URL classification.

Phase 10 WI-10.6 (gap #25). Mirrors typescript/src/services/mcp/
officialRegistry.ts:78 LOC. The registry at
``https://api.anthropic.com/mcp-registry/v0/servers`` lists "official"
MCP servers; we fetch the URL set at startup (fire-and-forget) so
telemetry / UI can flag a connection as "official" vs "third-party".

Behavior:
  - ``prefetch_official_mcp_urls()`` runs once per process, fires the
    fetch on a background task, and silently degrades on any failure
    (network down, env disabled, non-first-party provider).
  - ``is_official_mcp_url(url)`` answers the classification question
    against the in-memory set; returns False if the prefetch hasn't
    completed or returned empty.

Skipped when:
  * ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1``
  * ``CLAUDE_PROVIDER`` is not ``anthropic``
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_REGISTRY_URL = "https://api.anthropic.com/mcp-registry/v0/servers"
_REGISTRY_PARAMS = {"version": "latest", "visibility": "commercial"}
_REGISTRY_TIMEOUT_S = 5.0

# Module-state. Loaded once per process; subsequent prefetch calls are no-ops.
_official_urls: set[str] | None = None
_prefetch_task: asyncio.Task[None] | None = None


def _is_disabled() -> bool:
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "").strip() == "1":
        return True
    provider = os.environ.get("CLAUDE_PROVIDER", "anthropic").strip().lower()
    return provider != "anthropic"


def _normalize_url(url: str) -> str:
    """Drop query + trailing slash so equivalent vendor URLs collapse."""
    parsed = urlparse(url)
    path = parsed.path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return parsed._replace(query="", fragment="", path=path, netloc=parsed.netloc.lower()).geturl()


def prefetch_official_mcp_urls() -> None:
    """Fire-and-forget background prefetch of the registry URL set.

    Idempotent — only the first call kicks off the task; subsequent
    calls are no-ops. Failures are swallowed (the registry is
    telemetry-grade, not functional).
    """
    global _prefetch_task
    if _prefetch_task is not None:
        return
    if _is_disabled():
        _record_empty()
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No running loop; defer until a caller invokes us from one.
        return
    if not loop.is_running():
        return
    _prefetch_task = loop.create_task(_fetch_and_store())


async def _fetch_and_store() -> None:
    global _official_urls
    try:
        async with httpx.AsyncClient(timeout=_REGISTRY_TIMEOUT_S) as client:
            response = await client.get(
                _REGISTRY_URL,
                params=_REGISTRY_PARAMS,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            logger.debug(
                "Official MCP registry returned HTTP %d; skipping",
                response.status_code,
            )
            _record_empty()
            return
        payload = response.json()
    except Exception as exc:
        logger.debug("Official MCP registry prefetch failed: %s", exc)
        _record_empty()
        return
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, list):
        _record_empty()
        return
    urls: set[str] = set()
    for entry in servers:
        if isinstance(entry, dict):
            for key in ("url", "endpoint", "http_url"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    urls.add(_normalize_url(value))
                    break
    _official_urls = urls


def _record_empty() -> None:
    global _official_urls
    if _official_urls is None:
        _official_urls = set()


def is_official_mcp_url(url: str) -> bool:
    """Return True when ``url`` matches an entry in the prefetched
    registry (normalized). Returns False when the prefetch hasn't
    completed, is disabled, or the URL isn't in the set."""
    if _official_urls is None or not url:
        return False
    return _normalize_url(url) in _official_urls
