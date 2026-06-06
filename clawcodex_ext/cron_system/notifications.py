"""Cron scheduler notification helpers."""

from __future__ import annotations

from .models import CronTask


def build_missed_task_notification(missed: list[CronTask]) -> str:
    if not missed:
        return ""
    lines = ["The following one-shot scheduled tasks were missed while the scheduler was inactive:"]
    for task in missed:
        fence = _safe_fence(task.prompt)
        lines.extend([
            f"- {task.id}: {task.cron}",
            fence,
            task.prompt,
            fence,
        ])
    return "\n".join(lines)


def _safe_fence(text: str) -> str:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)
