from __future__ import annotations

import json
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _clipboard_read_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if not hasattr(context, "clipboard") or context.clipboard is None:
        return ToolResult(name="ClipboardRead", output={"content": ""})
    return ToolResult(name="ClipboardRead", output={"content": str(context.clipboard)})


ClipboardReadTool: Tool = build_tool(
    name="ClipboardRead",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_clipboard_read_call,
    prompt="Read from the clipboard.",
    description="Read from the clipboard.",
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)


def _clipboard_write_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    content = tool_input.get("content")
    if not isinstance(content, str):
        raise ToolInputError("content must be a string")
    context.clipboard = content
    return ToolResult(name="ClipboardWrite", output={"success": True})


ClipboardWriteTool: Tool = build_tool(
    name="ClipboardWrite",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    },
    call=_clipboard_write_call,
    prompt="Write content to the clipboard.",
    description="Write content to the clipboard.",
    max_result_size_chars=1000,
    is_read_only=lambda _input: False,
    is_concurrency_safe=lambda _input: False,
)


def _status_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(
        name="Status",
        output={
            "cwd": str(context.cwd),
            "workspace_root": str(context.workspace_root),
            "plan_mode": getattr(context, "plan_mode", False),
            "in_worktree": context.worktree_root is not None,
        },
    )


StatusTool: Tool = build_tool(
    name="Status",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_status_call,
    prompt="Get current session status.",
    description="Get current session status.",
    max_result_size_chars=10_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
