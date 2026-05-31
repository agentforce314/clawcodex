"""Downstream Cron tool implementations backed by persistent storage."""

from __future__ import annotations

from typing import Any

from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolResult

from .models import CronTask
from .parser import cron_to_human, parse_cron_expression
from .tasks import add_cron_task, read_cron_tasks, remove_cron_tasks


def _cron_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    cron = tool_input.get("cron")
    prompt = tool_input.get("prompt")
    if not isinstance(cron, str) or not cron.strip():
        raise ToolInputError("cron must be a non-empty string")
    if parse_cron_expression(cron) is None:
        raise ToolInputError("cron must be a valid five-field expression")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolInputError("prompt must be a non-empty string")

    recurring = bool(tool_input.get("recurring", True))
    durable = bool(tool_input.get("durable", False))
    task = add_cron_task(
        context.workspace_root,
        cron=cron.strip(),
        prompt=prompt,
        recurring=recurring,
        durable=durable,
    )
    return ToolResult(
        name="CronCreate",
        output={
            "id": task.id,
            "cron": task.cron,
            "humanSchedule": cron_to_human(task.cron),
            "recurring": task.recurring,
            "durable": task.durable,
            "nextFireAt": task.next_fire_at,
        },
    )


CronCreateTool: Tool = build_tool(
    name="CronCreate",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cron": {"type": "string"},
            "prompt": {"type": "string"},
            "recurring": {"type": "boolean"},
            "durable": {"type": "boolean"},
        },
        "required": ["cron", "prompt"],
    },
    call=_cron_create_call,
    prompt="Schedule a recurring or one-shot prompt.",
    description="Schedule a recurring or one-shot prompt.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    to_auto_classifier_input=lambda input_data: (
        f"{(input_data or {}).get('cron', '')}: {(input_data or {}).get('prompt', '')}"
    ),
)


def _cron_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    jobs = [_task_output(task) for task in read_cron_tasks(context.workspace_root)]
    return ToolResult(name="CronList", output={"jobs": jobs})


CronListTool: Tool = build_tool(
    name="CronList",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_cron_list_call,
    prompt="List scheduled cron jobs.",
    description="List scheduled cron jobs.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)


def _cron_delete_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    cron_id = tool_input.get("id")
    if not isinstance(cron_id, str) or not cron_id.strip():
        raise ToolInputError("id must be a non-empty string")
    existed = remove_cron_tasks(context.workspace_root, cron_id.strip())
    return ToolResult(name="CronDelete", output={"success": existed, "id": cron_id.strip()})


CronDeleteTool: Tool = build_tool(
    name="CronDelete",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    call=_cron_delete_call,
    prompt="Delete a scheduled cron job.",
    description="Delete a scheduled cron job.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("id", "") or "",
)


def _task_output(task: CronTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "cron": task.cron,
        "prompt": task.prompt,
        "humanSchedule": cron_to_human(task.cron),
        "recurring": task.recurring,
        "durable": task.durable,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "lastFiredAt": task.last_fired_at,
        "nextFireAt": task.next_fire_at,
        "expiresAt": task.expires_at,
    }
