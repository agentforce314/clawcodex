"""CronCreate / CronList / CronDelete — session-scoped scheduled prompts.

When the context carries a live ``cron_scheduler`` (the main agent-server
session), these tools register real firing jobs: the server worker's idle
branch pops due jobs and runs their prompts as internal turns between user
turns. Without a scheduler (subagents, SDK surfaces, bare tests) they fall
back to the historical inert in-memory ``context.crons`` dict so existing
consumers keep their behavior.

Semantics mirror docs/en/scheduled-tasks: 5-field vixie cron expressions,
8-character job IDs, a 50-job session cap, recurring jobs auto-expire after
7 days (one final fire, then self-delete), one-shots delete after firing,
and deterministic jitter derived from the job ID.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

_CRON_CREATE_PROMPT = """Schedule a prompt to run in this session on a cron cadence.

- `cron` is a standard 5-field expression (`minute hour day-of-month month day-of-week`),
  interpreted in the user's LOCAL timezone. Fields support `*`, single values,
  steps (`*/15`), ranges (`1-5`), and comma lists. Day-of-week uses 0 or 7 for
  Sunday. Extended syntax (`L`, `W`, `?`, name aliases) is not supported.
- `recurring: true` re-fires on every match and auto-expires 7 days after
  creation (one final fire, then the job deletes itself). `recurring: false`
  fires once at the next match, then deletes itself.
- Fired prompts run between turns, when the session is idle. Missed fires do
  not catch up: a job that came due mid-turn fires once when the turn ends.
- A session holds at most 50 jobs. Each job gets an 8-character ID for CronDelete.
"""


def _cron_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    cron = tool_input.get("cron")
    prompt = tool_input.get("prompt")
    if not isinstance(cron, str) or not cron.strip():
        raise ToolInputError("cron must be a non-empty string")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolInputError("prompt must be a non-empty string")
    recurring = bool(tool_input.get("recurring", True))
    durable = bool(tool_input.get("durable", False))

    scheduler = getattr(context, "cron_scheduler", None)
    if scheduler is None:
        # Legacy inert registry (subagents / SDK surfaces without a worker
        # loop to fire jobs) — record the request without scheduling. The
        # `inert` flag keeps the success shape honest: nothing will fire.
        cid = uuid.uuid4().hex[:12]
        context.crons[cid] = {
            "id": cid, "cron": cron, "prompt": prompt,
            "recurring": recurring, "durable": durable,
        }
        return ToolResult(
            name="CronCreate",
            output={"id": cid, "humanSchedule": cron, "recurring": recurring,
                    "durable": durable, "inert": True},
        )

    try:
        job = scheduler.create(cron.strip(), prompt, recurring=recurring,
                               durable=durable)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    return ToolResult(
        name="CronCreate",
        output={
            "id": job.id,
            "humanSchedule": scheduler.human_schedule(job.cron),
            "cron": job.cron,
            "recurring": job.recurring,
            "durable": job.durable,
            "nextFireAt": job.next_fire_at,
            "expiresAt": job.expires_at,
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
    prompt=_CRON_CREATE_PROMPT,
    description="Schedule a recurring or one-shot prompt to run in this session.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    # Mirrors TS CronCreateTool.toAutoClassifierInput.
    to_auto_classifier_input=lambda input_data: (
        f"{(input_data or {}).get('cron', '')}: {(input_data or {}).get('prompt', '')}"
    ),
)


def _cron_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    scheduler = getattr(context, "cron_scheduler", None)
    if scheduler is None:
        jobs = list(context.crons.values())
        jobs.sort(key=lambda x: x["id"])
        return ToolResult(name="CronList", output={"jobs": jobs})

    jobs = []
    for job in scheduler.list_jobs():
        entry = job.to_dict()
        entry["humanSchedule"] = scheduler.human_schedule(job.cron)
        jobs.append(entry)
    wakeup = scheduler.wakeup_info()
    return ToolResult(
        name="CronList",
        output={
            "jobs": jobs,
            "pendingWakeup": wakeup.to_dict() if wakeup else None,
        },
    )


CronListTool: Tool = build_tool(
    name="CronList",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_cron_list_call,
    prompt="List this session's scheduled cron jobs (and any pending dynamic-loop wakeup) with their IDs, schedules, and prompts.",
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
    scheduler = getattr(context, "cron_scheduler", None)
    if scheduler is None:
        existed = cid in context.crons
        context.crons.pop(cid, None)
        return ToolResult(name="CronDelete", output={"success": existed, "id": cid})
    existed = scheduler.delete(cid.strip())
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
    prompt="Cancel a scheduled cron job by its 8-character ID (from CronCreate or CronList).",
    description="Delete a scheduled cron job.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    # Mirrors TS CronDeleteTool.toAutoClassifierInput.
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("id", "") or "",
)
