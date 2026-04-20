from __future__ import annotations

import uuid
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _cron_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    cron = tool_input.get("cron")
    prompt = tool_input.get("prompt")
    if not isinstance(cron, str) or not cron.strip():
        raise ToolInputError("cron must be a non-empty string")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolInputError("prompt must be a non-empty string")
    recurring = bool(tool_input.get("recurring", True))
    durable = bool(tool_input.get("durable", False))

    cid = uuid.uuid4().hex[:12]
    context.crons[cid] = {"id": cid, "cron": cron, "prompt": prompt, "recurring": recurring, "durable": durable}
    return ToolResult(
        name="CronCreate",
        output={"id": cid, "humanSchedule": cron, "recurring": recurring, "durable": durable},
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
    prompt="Schedule a recurring or one-shot prompt (in-memory).",
    description="Schedule a recurring or one-shot prompt (in-memory).",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)


def _cron_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    jobs = list(context.crons.values())
    jobs.sort(key=lambda x: x["id"])
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
    cid = tool_input.get("id")
    if not isinstance(cid, str) or not cid.strip():
        raise ToolInputError("id must be a non-empty string")
    existed = cid in context.crons
    context.crons.pop(cid, None)
    return ToolResult(name="CronDelete", output={"success": existed, "id": cid})


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
)
