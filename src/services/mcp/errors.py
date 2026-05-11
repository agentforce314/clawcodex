from __future__ import annotations

import re
from typing import Any


# Detect a JSON-RPC error code as a digit-bounded numeric value, not a
# substring. ``"code":32600`` would otherwise substring-match against
# ``"code":-32600`` (Invalid Request), misclassifying a malformed-request
# error as session-expired. The negative-lookbehind ``(?<!-)`` excludes
# the negative sign; the trailing ``\b`` excludes longer-digit-suffix
# accidents (``-320013``).
_NEG32001_RE = re.compile(r'"code"\s*:\s*-32001\b')
_POS32600_RE = re.compile(r'"code"\s*:\s*(?<!-)32600\b')
# ``Session terminated`` only counts when it appears alongside a JSON-RPC
# code field carrying one of the recognized session-expiry codes (-32001
# or 32600). Earlier iteration matched any code, which would misclassify
# a -32602 (Invalid Params) error whose message text happened to be
# "Session terminated" as session-expired and trigger spurious reconnects.
_SESSION_TERMINATED_RE = re.compile(
    r'"code"\s*:\s*(?:-32001|(?<!-)32600)\b.*?"message"\s*:\s*"Session terminated"',
    re.DOTALL,
)


class McpAuthError(Exception):
    def __init__(self, server_name: str, message: str) -> None:
        super().__init__(message)
        self.server_name = server_name


class McpSessionExpiredError(Exception):
    def __init__(self, server_name: str) -> None:
        super().__init__(f'MCP server "{server_name}" session expired')
        self.server_name = server_name


class McpToolCallError(Exception):
    def __init__(
        self,
        message: str,
        telemetry_message: str = "",
        mcp_meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.telemetry_message = telemetry_message or message
        self.mcp_meta = mcp_meta


def is_mcp_session_expired_error(error: Exception) -> bool:
    """Detect MCP Streamable-HTTP session-expiry signal.

    Per the MCP Streamable-HTTP spec, a server-restart-induced session-expiry
    is signalled by HTTP 404 paired with a JSON-RPC error indicating session
    termination. Mirrors typescript/src/services/mcp/client.ts:
    isMcpSessionExpiredError, which checks both signals when both are
    available.

    Two surface shapes encountered in practice:

      1. **httpx-shaped error** (raw HTTP error before SDK envelope parsing):
         carries ``error.response.status_code`` / ``.status_code`` / ``.code``;
         ``error`` body contains the JSON-RPC error envelope.
      2. **SDK-wrapped ``McpToolCallError``** (SDK has already parsed the 404
         response and emitted a JSON-RPC error onto the read stream): the
         HTTP status is erased; only the JSON-RPC code + message survive.
         The ``mcp`` SDK at ``streamable_http.py:_send_session_terminated_error``
         emits ``code=32600, message="Session terminated"`` on a 404.
         Servers conforming to the canonical MCP spec emit ``code=-32001``.

    Detection logic:
      - If an HTTP status is exposed AND it is non-404, return False —
        deliberate non-session error (e.g. 500, 401 auth required).
      - Otherwise require the JSON-RPC error body (encoded in ``str(error)``)
        to carry one of the recognized session-expiry codes / messages.

    This dual-mode is the operative fix for the gap-analysis #8 blocker:
    in the mcp-PyPI-SDK adapter path, HTTP status is no longer reachable
    from the call-tool error, so we must trust the JSON-RPC signal alone.
    """
    # Normalize HTTP status (if present) to int.
    http_status = getattr(getattr(error, "response", None), "status_code", None)
    if http_status is None:
        http_status = getattr(error, "status_code", None)
    if http_status is None:
        http_status = getattr(error, "code", None)
    try:
        http_status = int(http_status) if http_status is not None else None
    except (TypeError, ValueError):
        http_status = None

    # If status is present and explicitly NOT 404, this is some other failure
    # (500, 401, etc.) and definitely not session-expiry — return False fast.
    if http_status is not None and http_status != 404:
        return False

    msg = str(error)
    # MCP spec code (negative). Regex match on a digit-bounded numeric
    # literal — substring matching would misfire on ``-320013`` etc.
    if _NEG32001_RE.search(msg):
        return True
    # mcp PyPI SDK code (positive 32600 + canonical message); the SDK uses
    # this on a 404 response, so this is the dominant shape today. The
    # negative-lookbehind in the regex prevents matching ``-32600`` (the
    # JSON-RPC "Invalid Request" code), which would otherwise cause every
    # malformed-request error to falsely trigger reconnect.
    if _POS32600_RE.search(msg):
        return True
    # ``"Session terminated"`` text alone is permissive; require it to
    # appear inside a JSON-RPC error envelope (i.e. alongside a ``code``
    # field) so a tool legitimately returning the phrase as content
    # doesn't get misclassified.
    if _SESSION_TERMINATED_RE.search(msg):
        return True
    return False
