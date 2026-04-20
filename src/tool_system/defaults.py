from __future__ import annotations

from .registry import ToolRegistry
from .tools import ALL_STATIC_TOOLS, make_agent_tool, make_tool_search_tool


def build_default_registry(
    *,
    include_user_tools: bool = True,
    provider: "Any | None" = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_STATIC_TOOLS:
        registry.register(tool)
    registry.register(make_agent_tool(registry, provider=provider))
    registry.register(make_tool_search_tool(registry))
    return registry
