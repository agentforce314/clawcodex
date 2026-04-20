from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult


def _task_stop_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    reason = tool_input.get("reason", "")
    task_id = tool_input.get("task_id")

    if isinstance(task_id, str) and task_id.strip():
        bg_tasks = getattr(context, "background_bash_tasks", None) or {}
        if task_id in bg_tasks:
            from src.tool_system.tools.bash.background import stop_background_bash

            stopped = stop_background_bash(context, task_id)
            return ToolResult(
                name="TaskStop",
                output={
                    "stopped": stopped,
                    "task_id": task_id,
                    "reason": reason,
                },
            )

    context.stop_requested = True
    return ToolResult(name="TaskStop", output={"stopped": True, "reason": reason})


TaskStopTool: Tool = build_tool(
    name="TaskStop",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
    call=_task_stop_call,
    prompt="""\
Stops a running background task by its ID.

- Takes a task_id parameter identifying the task to stop
- Returns a success or failure status
- Use this tool when you need to terminate a long-running task
""",
    description="Stop a running background task by its ID.",
    strict=True,
    max_result_size_chars=1000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
