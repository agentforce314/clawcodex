"""Downstream Cron tool implementations backed by persistent storage.

Implements F-22-G1 (kill switch) and F-22-G6 (rich prompt docs).
"""

from __future__ import annotations

from typing import Any

from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolResult

from .models import (
    CronJitterConfig,
    CronTask,
    is_cron_disabled,
    validate_jitter_config,
)
from .parser import cron_to_human, parse_cron_expression
from .tasks import add_cron_task, read_all_cron_tasks, remove_cron_tasks

# F-22-G1: keep in sync with `is_cron_disabled` for the in-process fast path.
CRON_DISABLED_MESSAGE = "Cron is disabled (CLAWCODEX_DISABLE_CRON is set)."

CRON_CREATE_PROMPT = """\
Schedule a recurring or one-shot prompt to run via the cron scheduler.

# Cron expression

Five fields, local time: `minute hour day-of-month month day-of-week`.
Examples:
  - `*/5 * * * *` — every 5 minutes
  - `0 9 * * 1-5` — 09:00 on weekdays
  - `0 0 1 * *` — midnight on the 1st of every month

# Recurring vs one-shot

  - `recurring: true` (default) — fires on every match, reschedules from the
    fire time. Auto-expires after `recurring_max_age_ms` (default 7 days) so
    forgotten jobs do not leak forever.
  - `recurring: false` — fires once and is deleted.

# Jitter

The scheduler applies deterministic per-task jitter to avoid thundering herd
on round wall-clock marks (e.g. `:00`, `:30`):
  - Recurring tasks: forward jitter, up to `recurring_cap_ms` (default 15 min)
    proportional to the interval between fires.
  - One-shot tasks: backward lead (early fire) up to `one_shot_max_ms`
    (default 90 s) when the fire minute matches `one_shot_minute_mod` (default
    30). This keeps `:00`/` `:30` user-pinned reminders from slamming inference.

Avoid scheduling many tasks on the same round mark; stagger via the cron
expression when possible.

# Durable vs session

  - `durable: true` (default) — persisted to `.claude/scheduled_tasks.json`
    and survives process restarts.
  - `durable: false` — kept in the active session only; never written to disk.
    Use for ephemeral follow-ups.

# Scope and limits

  - Maximum 50 scheduled jobs per workspace.
  - `permanent` is a system-only flag (assistant mode installer). CronCreate
    cannot set it; doing so will raise an error.
  - Setting `CLAWCODEX_DISABLE_CRON=1` disables all cron tools — the call
    returns a soft "Cron is disabled" result, not an error.
"""

CRON_LIST_PROMPT = """\
List all scheduled cron jobs (file-backed and session-only) for the current
workspace. Returns per-job `id`, `cron` expression, human-readable schedule,
`recurring`/`durable` flags, plus `createdAt`/`updatedAt`/`lastFiredAt`/
`nextFireAt`/`expiresAt` timestamps.

Use the returned `id` with CronDelete to remove a job. Field reference:
  - `permanent: true` jobs are system-installed (catch-up / morning-checkin /
    dream) and are exempt from auto-expiry — do not delete them.
  - Teammate / agent-scoped jobs (if any) only fire on the owning session.
"""

CRON_DELETE_PROMPT = """\
Delete a scheduled cron job by id. Use CronList first to look up the id; the
field is the 8-char hex returned by CronCreate / CronList.

Deletion is irreversible — the job, its run history, and any pending fire are
all removed. For recurring jobs the on-disk record is removed entirely (no
"paused" state). For session-only jobs the in-memory record is cleared.
"""


def _cron_disabled_result(tool_name: str) -> ToolResult:
    return ToolResult(
        name=tool_name,
        output={"success": False, "disabled": True, "message": CRON_DISABLED_MESSAGE},
    )


def _cron_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if is_cron_disabled():
        return _cron_disabled_result("CronCreate")

    cron = tool_input.get("cron")
    prompt = tool_input.get("prompt")
    if not isinstance(cron, str) or not cron.strip():
        raise ToolInputError("cron must be a non-empty string")
    if parse_cron_expression(cron) is None:
        raise ToolInputError("cron must be a valid five-field expression")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolInputError("prompt must be a non-empty string")

    # F-22-G4: CronCreate cannot set `permanent`. The flag is reserved for
    # the assistant-mode installer (write_if_missing).
    if tool_input.get("permanent") is True:
        raise ToolInputError(
            "permanent is a system-only flag and cannot be set via CronCreate"
        )

    recurring = bool(tool_input.get("recurring", True))
    durable = bool(tool_input.get("durable", False))
    task = add_cron_task(
        context.workspace_root,
        cron=cron.strip(),
        prompt=prompt,
        recurring=recurring,
        durable=durable,
        session_store=context.crons,
    )
    return ToolResult(
        name="CronCreate",
        output={
            "id": task.id,
            "cron": task.cron,
            "humanSchedule": cron_to_human(task.cron),
            "recurring": task.recurring,
            "durable": task.durable,
            "permanent": task.permanent,
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
    prompt=CRON_CREATE_PROMPT,
    description=CRON_CREATE_PROMPT.splitlines()[0].lstrip("# ").strip() or "Schedule a recurring or one-shot prompt.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,
    is_concurrency_safe=lambda _input: True,
    to_auto_classifier_input=lambda input_data: (
        f"{(input_data or {}).get('cron', '')}: {(input_data or {}).get('prompt', '')}"
    ),
)


def _cron_list_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if is_cron_disabled():
        return _cron_disabled_result("CronList")
    jobs = [_task_output(task) for task in read_all_cron_tasks(context.workspace_root, context.crons)]
    return ToolResult(name="CronList", output={"jobs": jobs})


CronListTool: Tool = build_tool(
    name="CronList",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_cron_list_call,
    prompt=CRON_LIST_PROMPT,
    description="List scheduled cron jobs.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)


def _cron_delete_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if is_cron_disabled():
        return _cron_disabled_result("CronDelete")
    cron_id = tool_input.get("id")
    if not isinstance(cron_id, str) or not cron_id.strip():
        raise ToolInputError("id must be a non-empty string")
    normalized_id = cron_id.strip()
    existed = remove_cron_tasks(context.workspace_root, normalized_id, context.crons)
    if not existed:
        raise ToolInputError(f"No scheduled job with id '{normalized_id}'")
    return ToolResult(name="CronDelete", output={"success": True, "id": normalized_id})


CronDeleteTool: Tool = build_tool(
    name="CronDelete",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    call=_cron_delete_call,
    prompt=CRON_DELETE_PROMPT,
    description="Delete a scheduled cron job by id.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: False,
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
        "permanent": task.permanent,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "lastFiredAt": task.last_fired_at,
        "nextFireAt": task.next_fire_at,
        "expiresAt": task.expires_at,
    }
