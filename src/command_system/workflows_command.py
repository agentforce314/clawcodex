"""``/workflows`` — list running and recent dynamic-workflow runs.

The headless-capable core of the workflow view (mirrors ``/tasks``): reads the
shared ``runtime_tasks`` registry and reports each ``local_workflow`` task with
its status, name, and live progress summary. Works on every surface without
touching ``ctx.ui``.

:func:`render_workflows_report` is the shared renderer — the command object
below wraps it for registry dispatch (headless / tests), and the agent-server's
``workflows`` control request (``src/server/agent_server.py``) returns the same
text to the Ink TUI. Per-run/per-agent controls (``kill_workflow_task`` /
``skip_workflow_agent`` / ``retry_workflow_agent`` in
``src/tasks/local_workflow.py``) remain available for a richer client view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.workflow.gating import is_workflows_enabled

from .types import CommandContext, InteractiveCommand, InteractiveOutcome

#: Shown when the registry has no workflow runs to report.
NO_WORKFLOW_RUNS_MESSAGE = (
    "No workflow runs. Start one with /deep-research or by asking for a workflow."
)


def render_workflows_report(registry: Any) -> Optional[str]:
    """Render every ``local_workflow`` run in ``registry`` as one text report
    (blank-line-separated run blocks), or ``None`` when there are no runs.

    Defensive per run: ``progress`` objects are mutated in place by engine
    threads (see ``LocalWorkflowTaskState.progress``), so one mid-mutation
    render must not take down the whole report.
    """
    if registry is None:
        return None
    try:
        runs = [t for t in registry.all() if getattr(t, "type", None) == "local_workflow"]
    except Exception:
        runs = []
    if not runs:
        return None
    from src.workflow.progress import render_run_lines

    blocks: list[str] = []
    for t in runs:
        try:
            lines = render_run_lines(t)
            run_id = getattr(t, "run_id", "") or ""
            if run_id and lines:
                lines[0] = f"{lines[0]}  (run: {run_id})"
            blocks.append("\n".join(lines))
        except Exception:
            name = getattr(t, "workflow_name", None) or "workflow"
            status = getattr(t, "status", "?") or "?"
            blocks.append(f"{name}  [{status}]  (progress unavailable)")
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class WorkflowsCommand(InteractiveCommand):
    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        tc = getattr(context, "tool_context", None)
        registry = getattr(tc, "runtime_tasks", None) if tc is not None else None
        if registry is None:
            return InteractiveOutcome(
                message="Workflows are unavailable on this surface.", display="system"
            )
        report = render_workflows_report(registry)
        if report is None:
            return InteractiveOutcome(message=NO_WORKFLOW_RUNS_MESSAGE, display="system")
        return InteractiveOutcome(message=report, display="system")


WORKFLOWS_COMMAND = WorkflowsCommand(
    name="workflows",
    description="List running and recent dynamic workflows",
    is_enabled=is_workflows_enabled,
    kind="workflow",
)


__all__ = [
    "NO_WORKFLOW_RUNS_MESSAGE",
    "WORKFLOWS_COMMAND",
    "WorkflowsCommand",
    "render_workflows_report",
]
