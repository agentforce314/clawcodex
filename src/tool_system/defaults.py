from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from .registry import ToolRegistry
from .tools import ALL_STATIC_TOOLS, make_agent_tool, make_tool_search_tool


# Keep the first request close to Claude Code's proven default surface.  Tools
# outside this set remain registered and dispatchable, but are discovered
# through ToolSearch before their full schemas are added to subsequent calls.
#
# Claude Code 2.1.215 advertised the equivalent 25-tool set in the matched
# pypi-server benchmark.  ReportFindings has no clawcodex equivalent, leaving
# 24 names here.
ESSENTIAL_INITIAL_TOOL_NAMES = frozenset({
    "Agent",
    "Bash",
    "CronCreate",
    "CronDelete",
    "CronList",
    "Edit",
    "EnterWorktree",
    "ExitWorktree",
    "NotebookEdit",
    "Read",
    "ScheduleWakeup",
    "SendMessage",
    "Skill",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Workflow",
    "Write",
})


def _apply_initial_loading_policy(tool):
    """Return a registry-local tool carrying the default loading policy."""
    if tool.name in ESSENTIAL_INITIAL_TOOL_NAMES or tool.always_load:
        return tool
    if tool.should_defer:
        return tool
    return replace(tool, should_defer=True)


def build_default_registry(
    *,
    include_user_tools: bool = True,
    provider: "Any | None" = None,
    get_available_mcp_servers: Callable[[], list[str]] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in ALL_STATIC_TOOLS:
        registry.register(_apply_initial_loading_policy(tool))
    registry.register(_apply_initial_loading_policy(
        make_agent_tool(
            registry,
            provider=provider,
            get_available_mcp_servers=get_available_mcp_servers,
        )
    ))
    registry.register(_apply_initial_loading_policy(make_tool_search_tool(registry)))

    # Dynamic workflows. Registered unconditionally (like the Agent tool, which
    # also needs the registry + provider); the tool's ``is_enabled`` is the
    # single runtime gate (``get_tools`` filters by it fresh), so a ``/config``
    # toggle of ``disable_workflows`` takes effect without rebuilding the registry.
    from .tools.workflow import make_workflow_tool

    registry.register(_apply_initial_loading_policy(
        make_workflow_tool(registry, provider=provider),
    ))

    return registry
