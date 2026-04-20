from __future__ import annotations

import re
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolResult


_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


def _enter_worktree_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.worktree_root is not None:
        raise ToolPermissionError("already in a worktree session")
    name = tool_input.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ToolInputError("name must be a non-empty string when provided")
        if len(name) > 64 or not _NAME_RE.match(name):
            raise ToolInputError("invalid worktree name")
        slug = name
    else:
        slug = "worktree"

    root = context.workspace_root / ".clawcodex" / "worktrees" / slug
    root.mkdir(parents=True, exist_ok=True)
    context.worktree_root = root
    context.cwd = root
    return ToolResult(
        name="EnterWorktree",
        output={
            "worktreePath": str(root),
            "worktreeBranch": None,
            "message": f"Created worktree at {root}. The session is now working in the worktree.",
        },
    )


EnterWorktreeTool: Tool = build_tool(
    name="EnterWorktree",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}},
    },
    call=_enter_worktree_call,
    prompt="Create an isolated worktree directory and switch session into it.",
    description="Create an isolated worktree directory and switch session into it.",
    strict=True,
    max_result_size_chars=100_000,
    is_destructive=lambda _input: True,
)


def _exit_worktree_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.worktree_root is None:
        raise ToolPermissionError("not in a worktree session")
    old = str(context.worktree_root)
    context.worktree_root = None
    context.cwd = context.workspace_root
    return ToolResult(
        name="ExitWorktree",
        output={"message": f"Exited worktree session ({old}). Returned to {context.workspace_root}."},
    )


ExitWorktreeTool: Tool = build_tool(
    name="ExitWorktree",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_exit_worktree_call,
    prompt="Exit the current worktree session and return to the original workspace.",
    description="Exit the current worktree session and return to the original workspace.",
    strict=True,
    max_result_size_chars=100_000,
    is_destructive=lambda _input: True,
)
