"""Event Protocol — interface for tool event emission.

This Protocol defines the contract for tool event objects passed
between components. The concrete implementation is in
src/tool_system/agent_loop.py (ToolEvent dataclass).

This allows src/api/query.py to interface with the tool system
without importing from upstream concrete implementations.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["ToolEventProtocol"]


class ToolEventProtocol(Protocol):
    """Protocol for tool-use / tool-result / tool-error events.

    Concrete implementation: src/tool_system/agent_loop.ToolEvent
    """

    @property
    def kind(self) -> str:
        """Event kind: "tool_use", "tool_result", or "tool_error"."""

    @property
    def tool_name(self) -> str:
        """Name of the tool involved in this event."""

    @property
    def tool_input(self) -> dict[str, Any] | None:
        """Input arguments for tool_use events."""

    @property
    def tool_output(self) -> Any | None:
        """Output result for tool_result / tool_error events."""

    @property
    def tool_use_id(self) -> str | None:
        """Unique ID linking a tool_use to its corresponding tool_result."""

    @property
    def is_error(self) -> bool:
        """True for tool_error events."""

    @property
    def error(self) -> str | None:
        """Error message for tool_error events."""