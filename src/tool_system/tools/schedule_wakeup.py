"""ScheduleWakeup — the dynamic /loop pacing tool.

In self-paced loop mode (a /loop with no fixed interval) the model calls
this after each iteration to schedule when the loop resumes: the prompt
fires once between turns after ``delaySeconds`` (clamped to [60, 3600]),
and calling with ``stop: true`` ends the loop immediately by clearing the
pending wakeup. One wakeup slot exists per session — scheduling again
replaces the previous one. Jitter never applies to wakeups, and pressing
Esc while the session waits clears the slot (the loop does not fire again).

If a wakeup-fired iteration ends without rescheduling or stopping, the
server schedules one fallback wakeup (~20 minutes) and ends the loop when
that iteration doesn't reschedule either — mirroring CC ≥2.1.202.
"""

from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

_SCHEDULE_WAKEUP_PROMPT = """Schedule when to resume work in /loop dynamic (self-paced) mode.

- Pass `delaySeconds` (clamped to [60, 3600]), the `prompt` to fire on wake-up,
  and a short `reason` explaining the chosen delay (shown to the user).
- Pass the same /loop input back via `prompt` each turn so the next firing
  repeats the task (e.g. "/loop check whether CI passed").
- Pick shorter delays while external state is actively changing (a build or
  deploy in flight) and longer ones (1200s+) when things are quiet.
- To end the loop, call this tool with `stop: true` (omit every other field) —
  the pending wakeup is cleared immediately and the loop does not fire again.
- One wakeup slot per session: scheduling again replaces the pending one.
"""


def _schedule_wakeup_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    scheduler = getattr(context, "cron_scheduler", None)
    if scheduler is None:
        return ToolResult(
            name="ScheduleWakeup",
            output={"error": "scheduled tasks are unavailable in this context"},
            is_error=True,
        )

    if bool(tool_input.get("stop", False)):
        had_pending = scheduler.clear_wakeup()
        return ToolResult(
            name="ScheduleWakeup",
            output={"stopped": True, "clearedPendingWakeup": had_pending},
        )

    delay = tool_input.get("delaySeconds")
    prompt = tool_input.get("prompt")
    reason = tool_input.get("reason")
    if not isinstance(delay, (int, float)) or isinstance(delay, bool):
        raise ToolInputError("delaySeconds must be a number (unless stop is true)")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ToolInputError("prompt must be a non-empty string (unless stop is true)")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolInputError("reason must be a non-empty string (unless stop is true)")

    try:
        wakeup = scheduler.set_wakeup(float(delay), prompt, reason.strip())
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    effective = max(0.0, wakeup.fire_at - scheduler.now_fn())
    return ToolResult(
        name="ScheduleWakeup",
        output={
            "scheduled": True,
            "delaySeconds": round(effective),
            "firesAt": wakeup.fire_at,
            "reason": wakeup.reason,
        },
    )


ScheduleWakeupTool: Tool = build_tool(
    name="ScheduleWakeup",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "delaySeconds": {
                "type": "number",
                "description": "Seconds from now to wake up. Clamped to [60, 3600]. Required unless stop is true.",
            },
            "prompt": {
                "type": "string",
                "description": "The /loop input to fire on wake-up (pass the same /loop input verbatim so the loop continues). Required unless stop is true.",
            },
            "reason": {
                "type": "string",
                "description": "One short sentence explaining the chosen delay; shown to the user. Required unless stop is true.",
            },
            "stop": {
                "type": "boolean",
                "description": "Set to true to end the dynamic loop immediately instead of scheduling another wakeup.",
            },
        },
    },
    call=_schedule_wakeup_call,
    prompt=_SCHEDULE_WAKEUP_PROMPT,
    description="Schedule (or stop) the next self-paced /loop wakeup.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    to_auto_classifier_input=lambda input_data: (
        "stop" if (input_data or {}).get("stop")
        else f"{(input_data or {}).get('delaySeconds', '')}s: {(input_data or {}).get('prompt', '')}"
    ),
)
