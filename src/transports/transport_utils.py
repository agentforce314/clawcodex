"""Transport factory for SDK remote-control sessions.

Port of ``typescript/src/cli/transports/transportUtils.ts``.

Public surface
--------------

* :class:`Transport` — typing.Protocol capturing the shape every transport
  in :mod:`src.transports` satisfies. Duck-typed; existing
  ``WebSocketTransport`` / ``HybridTransport`` / ``SSETransport`` are
  structurally compatible.
* :func:`get_transport_for_url` — selects a transport class based on URL
  scheme + the ``CLAUDE_CODE_USE_CCR_V2`` and
  ``CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2`` env vars. Mirrors the TS
  factory exactly.
* :func:`is_env_truthy` — TS ``isEnvTruthy`` parity (case-insensitive
  1/true/yes/on); shared with :mod:`src.transports.remote_io`.

See ``my-docs/get-parity-by-folder/cli-refactoring-plan.md`` §2.1 for
the source-of-truth design notes (signature adapter, Protocol shape).

Notes on the Transport Protocol
-------------------------------

``write`` is NOT in the Protocol. ``SSETransport`` has no ``write``
method (its write side is handled separately by
``CCRClient.write_event``). Including ``write`` in the Protocol would
make ``isinstance(sse, Transport)`` return False under
``@runtime_checkable`` and silently break the CCR v2 type check.
``RemoteIO`` enforces the write-capability check explicitly at
construction time.

``set_on_close`` takes ``Callable[[int | None], None]`` (the close code),
matching the real transports at ``websocket_transport.py:263`` /
``sse_transport.py:109``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import httpx

from src.transports.hybrid_transport import HybridTransport
from src.transports.sse_transport import SSETransport
from src.transports.websocket_transport import WebSocketTransport


@runtime_checkable
class Transport(Protocol):
    """Shape every src.transports transport satisfies (sans ``write``).

    The four common methods. ``write(...)`` is intentionally omitted —
    SSETransport doesn't expose it (the write side is CCRClient's job).
    """

    async def connect(self) -> None: ...
    def close(self) -> None: ...
    def set_on_data(self, callback: Callable[[str], None]) -> None: ...
    def set_on_close(self, callback: Callable[[int | None], None]) -> None: ...


def is_env_truthy(value: str | None) -> bool:
    """TS ``isEnvTruthy`` parity — 1/true/yes/on (case-insensitive)."""
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes", "on")


def get_transport_for_url(
    url: str | httpx.URL,
    *,
    headers: dict[str, str] | None = None,
    session_id: str | None = None,
    refresh_headers: Callable[[], dict[str, str]] | None = None,
) -> Transport:
    """Pick an appropriate transport for ``url`` + current env vars.

    Selection (mirrors TS ``getTransportForUrl``):

      1. ``CLAUDE_CODE_USE_CCR_V2`` truthy → ``SSETransport`` on
         ``<url>/worker/events/stream`` (scheme rewritten ws→http,
         wss→https).
      2. URL is ws/wss + ``CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2``
         truthy → ``HybridTransport``.
      3. URL is ws/wss → ``WebSocketTransport``.
      4. Otherwise: raise ``ValueError("Unsupported protocol: …")``.

    All three transport constructors take ``url: str``; callers may pass
    either ``str`` or ``httpx.URL`` and the factory normalizes to ``str``
    before constructing.
    """
    headers = dict(headers or {})
    url_str = url if isinstance(url, str) else str(url)
    parsed = httpx.URL(url_str)

    if is_env_truthy(os.environ.get("CLAUDE_CODE_USE_CCR_V2")):
        # Rewrite the scheme + append the /worker/events/stream path.
        # Use plain string manipulation to mirror the TS literal approach
        # and avoid httpx.URL.copy_with(path=) URL-encoding edge cases.
        sse_url_str = url_str
        if sse_url_str.startswith("wss://"):
            sse_url_str = "https://" + sse_url_str[len("wss://"):]
        elif sse_url_str.startswith("ws://"):
            sse_url_str = "http://" + sse_url_str[len("ws://"):]
        sse_url_str = sse_url_str.rstrip("/") + "/worker/events/stream"
        # SSETransport's auth-refresh callback is named `get_auth_headers`
        # (not `refresh_headers`). The factory adapts; renaming the
        # parameter is deferred per cli-gap-analysis.md §4.7.
        return SSETransport(
            sse_url_str,
            headers=headers,
            session_id=session_id,
            get_auth_headers=refresh_headers,
        )

    if parsed.scheme in ("ws", "wss"):
        if is_env_truthy(os.environ.get("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2")):
            return HybridTransport(
                url_str,
                headers=headers,
                session_id=session_id,
                refresh_headers=refresh_headers,
            )
        return WebSocketTransport(
            url_str,
            headers=headers,
            session_id=session_id,
            refresh_headers=refresh_headers,
        )

    raise ValueError(f"Unsupported protocol: {parsed.scheme}")
