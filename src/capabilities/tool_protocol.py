"""ToolSystem Protocol — interface for the tool registry and execution.

Phase 1: Stub with NotImplementedError.
This Protocol defines the contract for tool system operations.
Concrete implementation is in src/tool_system/registry.py and build_tool.py.

See: src/tool_system/agent_loop.py imports from tool_system.registry
"""

from typing import Protocol

__all__ = ["ToolSystemProtocol"]


class ToolSystemProtocol(Protocol):
    """Protocol for tool registry and tool execution.

    Implementors must provide:
      - get_tools() -> list[Tool]
      - find_tool_by_name(name) -> Tool | None
      - build_tool(tool_def) -> Tool
      - assemble_tool_pool() -> list[Tool]
    """

    def get_tools(self) -> "list[Tool]": ...  # pragma: no cover  # noqa: F821

    def find_tool_by_name(self, name: str) -> "Tool | None": ...  # pragma: no cover

    def build_tool(self, tool_def: "dict") -> "Tool": ...  # pragma: no cover

    def assemble_tool_pool(self) -> "list[Tool]": ...  # pragma: no cover