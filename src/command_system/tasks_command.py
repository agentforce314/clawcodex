"""tasks — ``/tasks`` background-task list (port of TS local-jsx, degraded).

TS ``/tasks`` (``commands/tasks/``) renders ``BackgroundTasksDialog`` — it **lists and
manages** (kill) background tasks. Python's TUI ``/tasks`` focuses a live task panel
(app-bound). This registry port provides the **headless list** — the running tasks ARE
reachable from the command surface via ``context.tool_context.runtime_tasks`` (the same
``RuntimeTaskRegistry`` tools write to) — and **drops management** (kill needs the
app/async ``Task.kill`` path). The TUI keeps its live panel (inversion).

Follows the output-style/``/mcp`` precedent: ``run()`` returns text **without touching
``ctx.ui``**, so it works on every surface; on the SDK (where ``tool_context`` is None)
it reports the list is unavailable rather than raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)


def _coerce(x: Any) -> Any:
    """Defensive: a ``.value``-bearing object → its ``.value``, else the value unchanged.
    ``TaskType``/``TaskStatus`` are ``Literal[str]`` today (already plain strings, so this
    just passes them through) — this only future-proofs against an enum/wrapper."""
    return getattr(x, "value", x)


def _format_task(t: Any) -> str:
    status = _coerce(getattr(t, "status", "")) or "?"
    label = getattr(t, "description", "") or _coerce(getattr(t, "type", "")) or "(task)"
    return f"[{status}] {label} (id: {getattr(t, 'id', '')})"


@dataclass(frozen=True)
class TasksCommand(InteractiveCommand):
    """List the running background tasks. Frozen + no new fields (the ``McpCommand``
    pattern); ``run()`` returns text without touching ``ctx.ui``."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        tc = getattr(context, "tool_context", None)
        registry = getattr(tc, "runtime_tasks", None) if tc is not None else None
        if registry is None:
            # SDK / listing surfaces have no live task registry.
            return InteractiveOutcome(
                message="Background tasks are unavailable on this surface.",
                display="system",
            )
        try:
            tasks = list(registry.all())
        except Exception:
            tasks = []
        if not tasks:
            return InteractiveOutcome(message="No background tasks.", display="system")
        lines = [f"• {_format_task(t)}" for t in tasks]
        return InteractiveOutcome(
            message="Background tasks:\n" + "\n".join(lines), display="system"
        )


TASKS_COMMAND = TasksCommand(
    name="tasks",
    description="List and manage background tasks",  # verbatim TS (port only LISTS)
    aliases=["bashes"],  # verbatim TS index.ts
)


__all__ = ["TASKS_COMMAND", "TasksCommand"]
