"""Footer/status pill label for running background tasks.

Port of ``typescript/src/tasks/pillLabel.ts``. The TUI's status line uses this
to render the "N background workflows" indicator. Kept dependency-free so any
widget can import it without pulling the task graph.
"""

from __future__ import annotations

from collections import Counter

from src.tasks_core import TaskStateBase, TaskType, is_terminal_task_status

_RUNNING = ("running", "pending")


def _running_counts(tasks: list[TaskStateBase]) -> Counter[TaskType]:
    counts: Counter[TaskType] = Counter()
    for task in tasks:
        if not is_terminal_task_status(task.status) and task.status in _RUNNING:
            if not getattr(task, "is_backgrounded", True):
                continue
            counts[task.type] += 1
    return counts


def workflow_pill_label(tasks: list[TaskStateBase]) -> str | None:
    """The 'N background workflow(s)' pill, or None when none are running."""
    n = _running_counts(tasks).get("local_workflow", 0)
    if n == 0:
        return None
    return "1 background workflow" if n == 1 else f"{n} background workflows"


def background_task_pill(tasks: list[TaskStateBase]) -> str | None:
    """A combined pill across background task types (workflows first)."""
    counts = _running_counts(tasks)
    parts: list[str] = []
    label = workflow_pill_label(tasks)
    if label:
        parts.append(label)
    agents = counts.get("local_agent", 0)
    if agents:
        parts.append(f"{agents} background agent" + ("" if agents == 1 else "s"))
    return " · ".join(parts) if parts else None
