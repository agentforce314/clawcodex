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
    registry.register(
        make_agent_tool(
            registry,
            provider=provider,
            get_available_mcp_servers=get_available_mcp_servers,
        )
    )
    registry.register(make_tool_search_tool(registry))

    # Dynamic workflows. Registered unconditionally (like the Agent tool, which
    # also needs the registry + provider); the tool's ``is_enabled`` is the
    # single runtime gate (``get_tools`` filters by it fresh), so a ``/config``
    # toggle of ``disable_workflows`` takes effect without rebuilding the registry.
    from .tools.workflow import make_workflow_tool

    registry.register(make_workflow_tool(registry, provider=provider))

    return registry
