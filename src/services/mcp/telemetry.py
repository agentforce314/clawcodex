"""MCP telemetry events — pluggable stub.

Phase 10 WI-10.5 (gap #26). TS emits ``tengu_mcp_*`` events at every
major flow boundary (oauth refresh failure, claudeai-mcp-connected,
session-expired, etc.). Python doesn't have a project-wide telemetry
sink wired up; this module provides a no-op default + a single
``register_sink`` injection point so a host can attach its observability
backend (Prometheus, OpenTelemetry, GrowthBook, custom) when ready.

API:
  emit(event_name, **properties)        — log + forward to the sink
  register_sink(callable)               — replace the default sink

Built-in event names (use these literals, not ad-hoc strings):
  MCP_OAUTH_REFRESH_FAILURE
  MCP_OAUTH_FLOW_ERROR
  MCP_CLAUDEAI_CONNECTED
  MCP_SESSION_EXPIRED
  MCP_AUTH_REQUIRED
  MCP_TOOL_CALL_TRUNCATED
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Stable event names; callers should reference these constants rather
# than passing ad-hoc strings so renames are find-and-replaceable.
MCP_OAUTH_REFRESH_FAILURE = "mcp_oauth_refresh_failure"
MCP_OAUTH_FLOW_ERROR = "mcp_oauth_flow_error"
MCP_CLAUDEAI_CONNECTED = "mcp_claudeai_connected"
MCP_SESSION_EXPIRED = "mcp_session_expired"
MCP_AUTH_REQUIRED = "mcp_auth_required"
MCP_TOOL_CALL_TRUNCATED = "mcp_tool_call_truncated"

_TelemetrySink = Callable[[str, dict[str, Any]], None]


def _default_sink(event: str, properties: dict[str, Any]) -> None:
    """Default sink: log at DEBUG. Hosts override via ``register_sink``."""
    logger.debug("mcp.telemetry %s %r", event, properties)


_sink: _TelemetrySink = _default_sink


def register_sink(sink: _TelemetrySink) -> None:
    """Replace the default DEBUG-log sink with a caller-provided one."""
    global _sink
    _sink = sink


def emit(event: str, **properties: Any) -> None:
    """Emit a telemetry event. Best-effort; sink exceptions are swallowed
    so a broken telemetry pipeline never breaks a real MCP call.
    """
    try:
        _sink(event, dict(properties))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mcp.telemetry sink raised: %s", exc)
