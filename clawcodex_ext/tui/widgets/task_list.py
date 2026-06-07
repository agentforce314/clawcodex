"""Task-list and background-task widgets.

Ports three closely-related TS components:

* ``components/TaskListV2.tsx`` — in-transcript TODO strip that
  renders ``Task`` items from :class:`AppState` with status icons
  (``pending`` / ``in_progress`` / ``completed`` / ``cancelled``).
* ``components/tasks/BackgroundTask.tsx`` — a compact single-line
  summary for a long-running background process (shell, sub-agent,
  remote session).
* ``components/AgentProgressLine.tsx`` — tree-style progress line
  emitted by the ``AgentTool`` UI; used when an agent delegates to a
  sub-agent and wants to show nested tool activity.

All three are plain :class:`textual.widgets.Static` so they slot
directly into the transcript or the status bar without needing a
container wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from rich.text import Text
from textual.widgets import Static


TaskStatus = Literal["pending", "in_progress", "completed", "cancelled", "failed"]


@dataclass
class Task:
    """A single TaskListV2 row."""

    id: str
    title: str
    status: TaskStatus = "pending"
    detail: str = ""
    children: list["Task"] = field(default_factory=list)


# Mapping from status → (icon, colour). Mirrors the glyph palette
# used by ``TaskListV2.tsx``.
_STATUS_STYLES: dict[TaskStatus, tuple[str, str]] = {
    "pending": ("○", "dim"),
    "in_progress": ("◐", "bold cyan"),
    "completed": ("✔", "bold green"),
    "cancelled": ("⊘", "dim yellow"),
    "failed": ("✖", "bold red"),
}


def render_task_tree(tasks: Iterable[Task], *, indent: int = 0) -> Text:
    """Render a task tree as a :class:`rich.text.Text` object."""

    out = Text()
    tasks = list(tasks)
    for idx, task in enumerate(tasks):
        icon, style = _STATUS_STYLES.get(task.status, ("•", ""))
        is_last = idx == len(tasks) - 1
        connector = ""
        if indent:
            connector = "    " * (indent - 1) + ("└── " if is_last else "├── ")
        out.append(connector, style="dim")
        out.append(f"{icon} ", style=style)
        out.append(task.title, style=style)
        if task.detail:
            out.append(f"  {task.detail}", style="dim")
        out.append("\n")
        if task.children:
            out.append_text(render_task_tree(task.children, indent=indent + 1))
    return out


class TaskListWidget(Static):
    """Renders a :class:`Task` tree in the transcript."""

    DEFAULT_CSS = """
    TaskListWidget {
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
        self._tasks: list[Task] = list(tasks or [])
        super().__init__(render_task_tree(self._tasks), markup=False)

    def set_tasks(self, tasks: Iterable[Task]) -> None:
        self._tasks = list(tasks)
        self.update(render_task_tree(self._tasks))

    @property
    def tasks(self) -> list[Task]:
        return list(self._tasks)

    def progress(self) -> tuple[int, int]:
        """Return ``(done, total)`` counted over leaf tasks only.

        A task is a "leaf" when it has no children; parent tasks act
        purely as grouping rows so they're excluded from the ratio.
        """

        def _count(tasks: Iterable[Task]) -> tuple[int, int]:
            done = total = 0
            for task in tasks:
                if task.children:
                    d, t = _count(task.children)
                    done += d
                    total += t
                    continue
                total += 1
                if task.status == "completed":
                    done += 1
            return done, total

        return _count(self._tasks)


class BackgroundTaskRow(Static):
    """Compact single-line background-task summary."""

    DEFAULT_CSS = """
    BackgroundTaskRow {
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        task_id: str,
        title: str,
        status: TaskStatus = "in_progress",
        detail: str = "",
    ) -> None:
        self._task_id = task_id
        self._title = title
        self._status: TaskStatus = status
        self._detail = detail
        super().__init__(self._build_text(), markup=False)

    def mark_status(self, status: TaskStatus, *, detail: str | None = None) -> None:
        self._status = status
        if detail is not None:
            self._detail = detail
        self.update(self._build_text())

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def task_id(self) -> str:
        return self._task_id

    def _build_text(self) -> Text:
        icon, style = _STATUS_STYLES.get(self._status, ("•", ""))
        out = Text(f"{icon} ", style=style)
        out.append(self._title, style="bold")
        if self._detail:
            out.append(f"  {self._detail}", style="dim")
        return out


class AgentProgressLine(Static):
    """Tree-style progress line used by :class:`AgentTool` UI.

    ``steps`` is a list of ``(label, status, detail)`` tuples rendered
    vertically; the widget is effectively a mini-TaskList that the
    agent owns for its lifetime. We expose :meth:`push_step` and
    :meth:`update_step` for the agent to drive without rebuilding
    the whole list.
    """

    DEFAULT_CSS = """
    AgentProgressLine {
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        header: str = "Delegated agent",
    ) -> None:
        self._header = header
        self._steps: list[tuple[str, TaskStatus, str]] = []
        super().__init__(self._build_text(), markup=False)

    def push_step(
        self,
        label: str,
        *,
        status: TaskStatus = "in_progress",
        detail: str = "",
    ) -> int:
        self._steps.append((label, status, detail))
        self.update(self._build_text())
        return len(self._steps) - 1

    def update_step(
        self,
        index: int,
        *,
        status: TaskStatus | None = None,
        detail: str | None = None,
    ) -> None:
        if not 0 <= index < len(self._steps):
            return
        label, old_status, old_detail = self._steps[index]
        self._steps[index] = (
            label,
            status if status is not None else old_status,
            detail if detail is not None else old_detail,
        )
        self.update(self._build_text())

    def _build_text(self) -> Text:
        out = Text()
        out.append(f"{self._header}\n", style="bold magenta")
        for idx, (label, status, detail) in enumerate(self._steps):
            icon, style = _STATUS_STYLES.get(status, ("•", ""))
            is_last = idx == len(self._steps) - 1
            connector = "└── " if is_last else "├── "
            out.append(connector, style="dim")
            out.append(f"{icon} ", style=style)
            out.append(label, style=style)
            if detail:
                out.append(f"  {detail}", style="dim")
            out.append("\n")
        return out


__all__ = [
    "AgentProgressLine",
    "BackgroundTaskRow",
    "Task",
    "TaskListWidget",
    "TaskStatus",
    "render_task_tree",
]
