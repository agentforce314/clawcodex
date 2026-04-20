from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _brief_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    text = tool_input.get("text")
    if not isinstance(text, str) or not text:
        raise ToolInputError("text must be a non-empty string")

    max_chars = tool_input.get("max_chars")
    if max_chars is not None:
        if not isinstance(max_chars, int) or max_chars < 1:
            raise ToolInputError("max_chars must be a positive integer")
        if len(text) > max_chars:
            preview = text[:max_chars] + "\u2026"
        else:
            preview = text
    else:
        preview = text

    context.outbox.append({"tool": "Brief", "text": preview})
    return ToolResult(name="Brief", output={"preview": preview, "text": text})


BriefTool: Tool = build_tool(
    name="Brief",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["text"],
    },
    call=_brief_call,
    prompt="Emit a brief informational message to the user.",
    description="Emit a brief informational message to the user.",
    strict=True,
    max_result_size_chars=10_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
