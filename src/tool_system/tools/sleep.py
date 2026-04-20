from __future__ import annotations

import time
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _sleep_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    seconds = tool_input["seconds"]
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        raise ToolInputError("seconds must be a number")
    if seconds < 0 or seconds > 30:
        raise ToolInputError("seconds must be between 0 and 30")
    time.sleep(float(seconds))
    return ToolResult(name="Sleep", output={"slept_seconds": float(seconds)})


SleepTool: Tool = build_tool(
    name="Sleep",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
    },
    call=_sleep_call,
    prompt="Sleep for a short duration.",
    description="Sleep for a short duration.",
    max_result_size_chars=1000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
