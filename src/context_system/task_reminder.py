"""Periodic task-tool reminder — port of ``getTaskReminderAttachments``.

Reference: ``typescript/src/utils/attachments.ts:3714-3771`` (gates + turn
counting via ``getTaskReminderTurnCounts`` at :3658, thresholds at :269) and
the ``task_reminder`` renderer in ``typescript/src/utils/messages.ts:2606``
(the reminder text itself).

Every ``TURNS_SINCE_WRITE`` assistant turns without a TaskCreate/TaskUpdate
call — throttled to at most one nudge per ``TURNS_BETWEEN_REMINDERS`` turns —
the model gets a ``<system-reminder>`` suggesting the task tools, with the
current task list attached. This was never ported, and its absence was one of
the reasons the interactive checklist HUD rarely appeared: once a session
started without todos, nothing ever steered the model back.

One DELIBERATE delta from the reference: non-interactive sessions get no
reminder at all. The TB2.1 harness measurements that damped the system-prompt
bullet (see ``prompt_assembly._TASK_TOOL_BULLET_NON_INTERACTIVE``) apply with
the same force here — headless bookkeeping is overhead nobody reads. The
reference nudges both surfaces.
"""
from __future__ import annotations

import os
from typing import Any

from .plan_mode import _is_human_turn  # noqa: F401  (re-exported for tests)
from .plan_mode import _message_text, _role

# Mirrors TODO_REMINDER_CONFIG (attachments.ts:269-272).
TURNS_SINCE_WRITE = 10
TURNS_BETWEEN_REMINDERS = 10

# The reminder's opening phrase doubles as the scan marker for the throttle
# (the reference matches ``attachment.type === 'task_reminder'``; here the
# persisted meta message's text is what survives into later turns). Known
# benign false positive: a UserPromptSubmit hook whose additionalContext
# echoes this phrase is also ``<system-reminder>``-wrapped and would read as
# a recent reminder — suppressing at most one nudge for ten turns. Role
# filtering already excludes the model quoting it.
_REMINDER_MARKER = "The task tools haven't been used recently"

_SYSTEM_REMINDER_PREFIX = "<system-reminder>"

# Verbatim from messages.ts:2617.
_REMINDER_TEXT = (
    "The task tools haven't been used recently. If you're working on tasks "
    "that would benefit from tracking progress, consider using TaskCreate "
    "to add new tasks and TaskUpdate to update task status (set to "
    "in_progress when starting, completed when done). Also consider "
    "cleaning up the task list if it has become stale. Only use these if "
    "relevant to the current work. This is just a gentle reminder - ignore "
    "if not applicable. Make sure that you NEVER mention this reminder to "
    "the user\n"
)


def _is_thinking_only(message: Any) -> bool:
    """Assistant message whose content is all thinking blocks.

    Mirrors the reference's ``isThinkingMessage`` skip in the turn counter —
    a thinking-only commit is not an assistant *turn*. Empty block content is
    skipped too: the reference's ``[].every(...)`` is vacuously true
    (messages.ts:3315), so an empty-content assistant commit does not count
    as a turn there either.
    """
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not isinstance(content, list):
        return False
    return all(
        (block.get("type") if isinstance(block, dict) else getattr(block, "type", None))
        in ("thinking", "redacted_thinking")
        for block in content
    )


def _uses_task_tools(message: Any) -> bool:
    """Assistant message carrying a TaskCreate/TaskUpdate tool_use block."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            name = block.get("name")
        else:
            block_type = getattr(block, "type", None)
            name = getattr(block, "name", None)
        if block_type == "tool_use" and name in ("TaskCreate", "TaskUpdate"):
            return True
    return False


def _is_task_reminder_attachment(message: Any) -> bool:
    if _role(message) != "user":
        return False
    text = _message_text(message)
    return text.lstrip().startswith(_SYSTEM_REMINDER_PREFIX) and _REMINDER_MARKER in text


def get_task_reminder_turn_counts(messages: list[Any]) -> tuple[int, int]:
    """(assistant turns since last TaskCreate/TaskUpdate, since last reminder).

    Exact port of ``getTaskReminderTurnCounts`` (attachments.ts:3658-3711):
    walk backwards; thinking-only assistant messages don't count as turns;
    the turn that itself carries the task tool_use is checked BEFORE the
    counter increments, so it contributes zero; each counter freezes once its
    event is found; stop early when both are.
    """
    mgmt_found = False
    reminder_found = False
    turns_since_mgmt = 0
    turns_since_reminder = 0

    for message in reversed(messages):
        if _role(message) == "assistant":
            if _is_thinking_only(message):
                continue
            if not mgmt_found and _uses_task_tools(message):
                mgmt_found = True
            if not mgmt_found:
                turns_since_mgmt += 1
            if not reminder_found:
                turns_since_reminder += 1
        elif not reminder_found and _is_task_reminder_attachment(message):
            reminder_found = True

        if mgmt_found and reminder_found:
            break

    return turns_since_mgmt, turns_since_reminder


def _format_task_list(tasks: dict[str, Any] | None) -> str:
    """``#id. [status] subject`` per task — messages.ts:2613-2615.

    Skips ``metadata._internal`` entries, the same defense-in-depth filter
    TaskList applies (tasks_v2.py) — the reminder must not list a task the
    listing tool itself would hide.
    """
    if not tasks:
        return ""
    lines: list[str] = []
    for task in tasks.values():
        if not isinstance(task, dict):
            continue
        if (task.get("metadata") or {}).get("_internal"):
            continue
        lines.append(
            f"#{task.get('id', '?')}. [{task.get('status', 'pending')}] {task.get('subject', '')}"
        )
    return "\n".join(lines)


def build_task_reminder_attachment(
    messages: list[Any],
    *,
    tools: list[Any] | None = None,
    tasks: dict[str, Any] | None = None,
) -> list[str]:
    """The reminder text due this turn, or ``[]``.

    ``tools`` is the live tool list (registry contents) — the reference skips
    when TaskUpdate is not in the toolkit (:3739). Its OTHER toolkit gate —
    skip when Brief is present (:3731, the coworker surface where
    SendUserMessage owns communication) — is deliberately NOT ported:
    clawcodex registers Brief unconditionally in the default registry, so
    membership carries no surface information and the transplanted gate
    would simply disable the reminder everywhere (measured: it did, caught
    by the wiring test). If a coworker-style surface ever lands, gate on
    whatever marks that surface, not on Brief's presence.

    ``tasks`` is ``tool_context.tasks``, the TaskCreate store, listed in the
    reminder body exactly as the reference lists ``listTasks()``.
    """
    from src.bootstrap.state import get_is_non_interactive_session
    from src.utils.task_flags import is_todo_v2_enabled

    # Parity gate (:3718) — the reminder names TaskCreate/TaskUpdate, which
    # only the TaskV2 surface has.
    if not is_todo_v2_enabled():
        return []

    # DELIBERATE delta — headless stays nudge-free (module docstring).
    if get_is_non_interactive_session():
        return []

    # Parity gates: ant users (:3722), reminder kill-switch (messages.ts:2610).
    if os.environ.get("USER_TYPE") == "ant":
        return []
    if os.environ.get("OPENCLAUDE_DISABLE_TOOL_REMINDERS", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return []

    if not messages:
        return []

    tool_names = set()
    for tool in tools or []:
        name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        if name:
            tool_names.add(str(name))

    if "TaskUpdate" not in tool_names:
        return []

    turns_since_mgmt, turns_since_reminder = get_task_reminder_turn_counts(messages)
    if turns_since_mgmt < TURNS_SINCE_WRITE or turns_since_reminder < TURNS_BETWEEN_REMINDERS:
        return []

    text = _REMINDER_TEXT
    task_items = _format_task_list(tasks)
    if task_items:
        text += f"\n\nHere are the existing tasks:\n\n{task_items}"

    return [text]


__all__ = [
    "TURNS_BETWEEN_REMINDERS",
    "TURNS_SINCE_WRITE",
    "build_task_reminder_attachment",
    "get_task_reminder_turn_counts",
]
