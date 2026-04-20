from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult


def _structured_output_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    context.outbox.append({"tool": "StructuredOutput", "structured_output": tool_input})
    return ToolResult(
        name="StructuredOutput",
        output={
            "data": "Structured output provided successfully",
            "structured_output": tool_input,
        },
    )


StructuredOutputTool: Tool = build_tool(
    name="StructuredOutput",
    input_schema={"type": "object", "additionalProperties": True},
    call=_structured_output_call,
    prompt="Return a final response as structured JSON.",
    description="Return a final response as structured JSON.",
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
