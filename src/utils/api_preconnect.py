"""WI-4.2: warm the TCP+TLS handshake to the Anthropic API.

Mirrors TS ``utils/apiPreconnect.ts``. Fires a HEAD request to
``https://api.anthropic.com`` during init so the handshake (~100-200ms
on cold connections) overlaps with the rest of the bootstrap. The
connection is discarded; the win is in the OS DNS cache + TCP/TLS
session resumption that benefit the first real API call.

**Why threading.Thread, not asyncio.** ``httpx`` doesn't have a
"fire-and-forget without an event loop" mode. A daemon thread runs the
HEAD request while the main thread continues bootstrap; if the request
takes too long the daemon is abandoned at process exit (no blocking).

Skipped when:
  * ``ANTHROPIC_BASE_URL`` is set (custom endpoint — preconnecting to
    ``api.anthropic.com`` would be wasted work).
  * ``HTTP_PROXY`` / ``HTTPS_PROXY`` env vars are configured (the warm
    handshake would target the proxy, not the API).
  * ``CLAUDE_CODE_DISABLE_API_PRECONNECT`` is truthy (escape hatch).
"""

from __future__ import annotations

import os
import threading

__all__ = [
    "PreconnectHandle",
    "should_skip_preconnect",
    "start_api_preconnect",
]


_DEFAULT_PRECONNECT_URL = "https://api.anthropic.com"
_PRECONNECT_TIMEOUT_S = 10.0


class PreconnectHandle:
    """Opaque handle holding the daemon thread reference.

    Returned synchronously from ``start_api_preconnect`` so callers can
    optionally ``join`` for diagnostics. Production code never joins —
    the handshake is fire-and-forget.
    """

    __slots__ = ("thread", "skipped")

    def __init__(self, thread: threading.Thread | None, skipped: bool = False) -> None:
        self.thread = thread
        self.skipped = skipped


def should_skip_preconnect() -> bool:
    """Decide whether to fire the preconnect, based on env state.

    Skip cases:
      1. ``ANTHROPIC_BASE_URL`` set — user has overridden the endpoint.
      2. ``HTTP_PROXY`` / ``HTTPS_PROXY`` set — warmed handshake would
         hit the proxy, not the API.
      3. ``CLAUDE_CODE_DISABLE_API_PRECONNECT`` truthy — escape hatch.
    """
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return True
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        return True
    if os.environ.get("CLAUDE_CODE_DISABLE_API_PRECONNECT", "").strip().lower() in {
        "1", "true", "yes"
    }:
        return True
    return False


def start_api_preconnect(url: str = _DEFAULT_PRECONNECT_URL) -> PreconnectHandle:
    """Spawn a daemon thread that fires HEAD ``url``; return immediately.

    The thread completes (or times out) on its own. ``daemon=True`` so a
    slow handshake never blocks process exit.
    """
    if should_skip_preconnect():
        return PreconnectHandle(thread=None, skipped=True)

    def _do_preconnect() -> None:
        # Lazy import inside the thread so importing this module doesn't
        # pull in httpx on the cold-start path.
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return
        try:
            with httpx.Client(timeout=_PRECONNECT_TIMEOUT_S) as client:
                # HEAD warms the handshake without transferring a body.
                # 4xx/5xx are fine — we just want the TCP+TLS session.
                client.head(url)
        except Exception:
            # Best-effort: never let preconnect failures bubble up.
            pass

    thread = threading.Thread(
        target=_do_preconnect,
        name="api-preconnect",
        daemon=True,
    )
    thread.start()
    return PreconnectHandle(thread=thread, skipped=False)
