from __future__ import annotations

from typing import Any


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
    if not hasattr(error, "args") or not error.args:
        return False
    msg = str(error)
    if '"code":-32001' in msg or '"code": -32001' in msg:
        return True
    return False
