from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from src.utils.task_flags import is_todo_v2_enabled


def _todo_write_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    todos = tool_input.get("todos")
    if not isinstance(todos, list):
        raise ToolInputError("todos must be an array")

    old = list(context.todos)
    all_done = True
    normalized: list[dict[str, Any]] = []
    for i, t in enumerate(todos):
        if not isinstance(t, dict):
            raise ToolInputError(f"todos[{i}] must be an object")
        content = t.get("content")
        status = t.get("status")
        active_form = t.get("activeForm")
        if not isinstance(content, str) or not content.strip():
            raise ToolInputError(f"todos[{i}].content must be a non-empty string")
        if status not in {"pending", "in_progress", "completed"}:
            raise ToolInputError(f"todos[{i}].status must be pending|in_progress|completed")
        if not isinstance(active_form, str) or not active_form.strip():
            raise ToolInputError(f"todos[{i}].activeForm must be a non-empty string")
        all_done = all_done and status == "completed"
        normalized.append({"content": content, "status": status, "activeForm": active_form})

    context.todos = [] if all_done else normalized
    return ToolResult(
        name="TodoWrite",
        output={"oldTodos": old, "newTodos": normalized},
    )


TodoWriteTool: Tool = build_tool(
    name="TodoWrite",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {"type": "string", "minLength": 1},
                    },
                    "required": ["content", "status", "activeForm"],
                },
            }
        },
        "required": ["todos"],
    },
    call=_todo_write_call,
    prompt="Update the current todo list for this session.",
    description="Update the current todo list for this session.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    is_enabled=lambda: not is_todo_v2_enabled(),
)
