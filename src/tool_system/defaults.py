from __future__ import annotations

from typing import Any, Callable

from .registry import ToolRegistry
from .tools import ALL_STATIC_TOOLS, make_agent_tool, make_tool_search_tool


def build_default_registry(
    *,
    include_user_tools: bool = True,
    provider: "Any | None" = None,
    get_available_mcp_servers: Callable[[], list[str]] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_STATIC_TOOLS:
        registry.register(tool)

    # Register extension tools (二开 tools that are not in upstream).
    try:
        from extensions.tool_system_ext.registration import EXTENSION_TOOLS
        for t in EXTENSION_TOOLS:
            registry.register(t)
    except ImportError:
        pass

    registry.register(
        make_agent_tool(
            registry,
            provider=provider,
            get_available_mcp_servers=get_available_mcp_servers,
        )
    )
    registry.register(make_tool_search_tool(registry))
    return registry
