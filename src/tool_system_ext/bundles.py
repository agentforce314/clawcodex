from __future__ import annotations

"""
Tool Bundle Definitions

Defines 4 tool loading modes for agents:
- bare: Zero tools (pure reasoning agent)
- default: Default bundle (bash, edit, read, search)
- clawcodex: All native built-in tools
- all: All available tools
"""

from typing import Final

# Bundle definitions: bundle_name -> list of tool names
TOOL_BUNDLES: dict[str, list[str]] = {
    "default": [
        "Bash",
        "Edit",
        "Write",
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
    ],
    "clawcodex": [
        # All native built-in tools
        "AskUserQuestion",
        "Bash",
        "Brief",
        "ClipboardRead",
        "ClipboardWrite",
        "Config",
        "CronCreate",
        "CronDelete",
        "CronList",
        "Edit",
        "EnterPlanMode",
        "EnterWorktree",
        "ExitPlanMode",
        "ExitWorktree",
        "Glob",
        "Grep",
        "LSP",
        "ListMcpResources",
        "MCPTool",
        "NotebookEdit",
        "ReadMcpResource",
        "Read",
        "SendMessage",
        "SendUserMessage",
        "Skill",
        "Sleep",
        "Status",
        "StructuredOutput",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "TaskUpdate",
        "TeamCreate",
        "TeamDelete",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
        "Write",
        # Internal tools
        "Agent",
        "ToolSearch",
    ],
}

# Mode to bundle names mapping
MODE_BUNDLES: dict[str, list[str]] = {
    "bare": [],
    "default": ["default"],
    "clawcodex": ["clawcodex"],
    "all": list(TOOL_BUNDLES.keys()),
}

# All available bundle names
ALL_BUNDLE_NAMES: list[str] = list(TOOL_BUNDLES.keys())


def get_bundle_tools(bundle_name: str) -> list[str]:
    """Get tool names for a bundle, returns empty list if bundle not found."""
    return list(TOOL_BUNDLES.get(bundle_name, []))


def get_all_bundle_tools() -> list[str]:
    """Get all tool names across all bundles (deduped)."""
    seen: set[str] = set()
    result: list[str] = []
    for tools in TOOL_BUNDLES.values():
        for t in tools:
            if t not in seen:
                seen.add(t)
                result.append(t)
    return result