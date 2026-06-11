"""``/workflows`` — list running and recent dynamic-workflow runs.

The headless-capable core of the workflow view (mirrors ``/tasks``): reads the
shared ``runtime_tasks`` registry and reports each ``local_workflow`` task with
its status, name, and live progress summary. Works on every surface without
touching ``ctx.ui``.

The rich TUI drill-down (phases → agents with the ``p``/``x``/``r``/``s`` key
bindings from the spec) is a follow-up; the per-run/per-agent control API it
needs already exists (``kill_workflow_task`` / ``skip_workflow_agent`` /
``retry_workflow_agent`` in ``src/tasks/local_workflow.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.workflow.gating import is_workflows_enabled

from .types import CommandContext, InteractiveCommand, InteractiveOutcome


def _format_workflow(t: Any) -> str:
    status = getattr(t, "status", "?") or "?"
    name = getattr(t, "workflow_name", "") or "workflow"
    summary = getattr(t, "summary", None) or ""
    run_id = getattr(t, "run_id", "") or ""
    tail = f" — {summary}" if summary else ""
    return f"[{status}] {name}{tail} (run: {run_id})"


@dataclass(frozen=True)
class WorkflowsCommand(InteractiveCommand):
    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        tc = getattr(context, "tool_context", None)
        registry = getattr(tc, "runtime_tasks", None) if tc is not None else None
        if registry is None:
            return InteractiveOutcome(
                message="Workflows are unavailable on this surface.", display="system"
            )
        try:
            runs = [t for t in registry.all() if getattr(t, "type", None) == "local_workflow"]
        except Exception:
            runs = []
        if not runs:
            return InteractiveOutcome(
                message="No workflow runs. Start one with /deep-research or by asking for a workflow.",
                display="system",
            )
        from src.workflow.progress import render_run_lines

        blocks: list[str] = []
        for t in runs:
            lines = render_run_lines(t)
            run_id = getattr(t, "run_id", "") or ""
            if run_id and lines:
                lines[0] = f"{lines[0]}  (run: {run_id})"
            blocks.append("\n".join(lines))
        return InteractiveOutcome(message="\n\n".join(blocks), display="system")


WORKFLOWS_COMMAND = WorkflowsCommand(
    name="workflows",
    description="List running and recent dynamic workflows",
    is_enabled=is_workflows_enabled,
    kind="workflow",
)


__all__ = ["WORKFLOWS_COMMAND", "WorkflowsCommand"]
