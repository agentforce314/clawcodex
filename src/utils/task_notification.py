"""``<task-notification>`` XML emitter — Chunk D / WI-3.1.

Mirrors the ``enqueueAgentNotification`` body in
``typescript/src/tasks/LocalAgentTask/LocalAgentTask.tsx:197-262``. Builds
the chapter-shaped envelope and pushes it onto the global pending-
notification queue (Chunk D / WI-3.3 will drain that queue at tool-round
boundaries inside the parent agent's run loop).

WI-3.2 (the ``notified`` flag check-and-set) is folded into this module's
``enqueue_agent_notification`` body: the registry update is atomic, and
the function refuses to enqueue when the task is already ``notified=True``.
This is the primary guard against duplicate XML deliveries — a task that
flips terminal between two notification sweeps still produces exactly
one envelope.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, TYPE_CHECKING

from src.constants.xml import (
    DURATION_MS_TAG,
    OUTPUT_FILE_TAG,
    RESULT_TAG,
    STATUS_TAG,
    SUMMARY_TAG,
    TASK_ID_TAG,
    TASK_NOTIFICATION_TAG,
    TOOL_USE_ID_TAG,
    TOOL_USES_TAG,
    TOTAL_TOKENS_TAG,
    USAGE_TAG,
)
from src.utils.message_queue_manager import enqueue_pending_notification

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry


NotificationStatus = Literal["completed", "failed", "killed"]


def _build_summary(description: str, status: NotificationStatus, error: str | None) -> str:
    """Mirror TS LocalAgentTask.tsx:246 summary phrasing exactly."""
    if status == "completed":
        return f'Agent "{description}" completed'
    if status == "failed":
        err = error or "Unknown error"
        return f'Agent "{description}" failed: {err}'
    return f'Agent "{description}" was stopped'


def build_task_notification_xml(
    *,
    task_id: str,
    description: str,
    status: NotificationStatus,
    output_file: str,
    error: str | None = None,
    final_message: str | None = None,
    usage: dict[str, int] | None = None,
    tool_use_id: str | None = None,
) -> str:
    """Produce the ``<task-notification>`` envelope for a terminal task.

    Pure function — no registry mutation, no queue push. ``enqueue_agent_notification``
    composes this with the WI-3.2 check-and-set; tests use it directly
    for snapshot comparisons.

    Format matches TS LocalAgentTask.tsx:252-257 byte-for-byte (modulo
    optional sections that disappear when their inputs are absent). All
    values are rendered as-is (no XML escaping) — the chapter shape
    treats these as model-facing user content, and TS does the same
    raw concatenation. Callers are responsible for not embedding
    closing tags inside summary/result text.
    """
    summary = _build_summary(description, status, error)
    tool_use_line = (
        f"\n<{TOOL_USE_ID_TAG}>{tool_use_id}</{TOOL_USE_ID_TAG}>"
        if tool_use_id
        else ""
    )
    result_section = (
        f"\n<{RESULT_TAG}>{final_message}</{RESULT_TAG}>"
        if final_message
        else ""
    )
    if usage is not None:
        usage_section = (
            f"\n<{USAGE_TAG}>"
            f"<{TOTAL_TOKENS_TAG}>{usage.get('total_tokens', 0)}</{TOTAL_TOKENS_TAG}>"
            f"<{TOOL_USES_TAG}>{usage.get('tool_uses', 0)}</{TOOL_USES_TAG}>"
            f"<{DURATION_MS_TAG}>{usage.get('duration_ms', 0)}</{DURATION_MS_TAG}>"
            f"</{USAGE_TAG}>"
        )
    else:
        usage_section = ""
    return (
        f"<{TASK_NOTIFICATION_TAG}>\n"
        f"<{TASK_ID_TAG}>{task_id}</{TASK_ID_TAG}>{tool_use_line}\n"
        f"<{OUTPUT_FILE_TAG}>{output_file}</{OUTPUT_FILE_TAG}>\n"
        f"<{STATUS_TAG}>{status}</{STATUS_TAG}>\n"
        f"<{SUMMARY_TAG}>{summary}</{SUMMARY_TAG}>{result_section}{usage_section}\n"
        f"</{TASK_NOTIFICATION_TAG}>"
    )


def enqueue_agent_notification(
    *,
    task_id: str,
    description: str,
    status: NotificationStatus,
    output_file: str,
    registry: "RuntimeTaskRegistry",
    error: str | None = None,
    final_message: str | None = None,
    usage: dict[str, int] | None = None,
    tool_use_id: str | None = None,
) -> bool:
    """Atomically check-and-set the ``notified`` flag, then enqueue.

    WI-3.2 contract: this is the single duplicate-delivery guard. The
    ``runtime_tasks.update`` mutator atomically reads ``prev.notified``
    and (if False) returns a new state with ``notified=True``. The XML
    is enqueued only when the mutator's pre-state had ``notified=False``.

    Returns True iff a notification was actually enqueued.

    Two concurrent callers on the same ``task_id`` race against the
    registry RLock; the second call sees ``notified=True`` and returns
    False without touching the queue.
    """
    # Local import to defer the cycle: ``local_agent`` imports
    # transcript stuff that imports back through the agent module.
    from src.tasks.local_agent import LocalAgentTaskState

    should_enqueue = False

    def _mark_notified(prev: Any) -> Any:
        nonlocal should_enqueue
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if prev.notified:
            return prev
        should_enqueue = True
        return replace(prev, notified=True)

    registry.update(task_id, _mark_notified)

    if not should_enqueue:
        return False

    xml = build_task_notification_xml(
        task_id=task_id,
        description=description,
        status=status,
        output_file=output_file,
        error=error,
        final_message=final_message,
        usage=usage,
        tool_use_id=tool_use_id,
    )
    enqueue_pending_notification(value=xml, mode="task-notification")
    return True


__all__ = [
    "NotificationStatus",
    "build_task_notification_xml",
    "enqueue_agent_notification",
]
