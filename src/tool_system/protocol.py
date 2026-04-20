from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

if TYPE_CHECKING:
    from .context import ToolContext


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    tool_use_id: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: Any
    is_error: bool = False
    tool_use_id: Optional[str] = None
    content_type: Literal["text", "json"] = "json"
    new_messages: list[Any] | None = None
    context_modifier: Callable[["ToolContext"], "ToolContext"] | None = None
    mcp_meta: dict[str, Any] | None = None

    @property
    def data(self) -> Any:
        return self.output
