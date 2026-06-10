"""``local_workflow`` task type — surfaces a running workflow as a background task.

Mirrors the ``local_agent`` lifecycle (``src/tasks/local_agent.py``) but for a
whole workflow run. The state holds a live reference to the engine's
``WorkflowRun`` (loose-typed to avoid an import cycle) and its
``WorkflowProgress`` snapshot; the named API reaches through the run to stop the
whole workflow (``kill_workflow_task``) or one in-flight agent
(``skip_workflow_agent``) via the per-agent abort controllers the engine
exposes. ``retry_workflow_agent`` is reserved — re-spawning a single agent
mid-run needs engine support that does not exist yet, so it currently reports
"unsupported" rather than silently doing nothing.

All mutations route through ``registry.update`` with synchronous mutators
(the A6/C5 contract: never await under the registry lock).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TYPE_CHECKING

from src.tasks_core import TaskStateBase, is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class LocalWorkflowTaskState(TaskStateBase):
    type: Literal["local_workflow"] = "local_workflow"  # type: ignore[assignment]
    run_id: str = ""
    workflow_name: str = ""
    summary: str | None = None
    #: Live ``WorkflowProgress`` (mutated in place by the engine) — the TUI reads
    #: phases/agents/tokens off it. Loose-typed to avoid importing the engine.
    progress: Any = field(default=None, compare=False)
    #: The live ``WorkflowRun``, used to reach per-agent / run abort controllers.
    run: Any = field(default=None, repr=False, compare=False)
    result: Any = None
    error: str | None = None
    is_backgrounded: bool = True
    is_paused: bool = False
    retain: bool = False
    evict_after: float | None = None


def is_local_workflow_task(state: Any) -> bool:
    return isinstance(state, LocalWorkflowTaskState)


# ── lifecycle ─────────────────────────────────────────────────────────────────


def register_workflow_task(
    *,
    task_id: str,
    run_id: str,
    workflow_name: str,
    description: str,
    output_file: str,
    progress: Any,
    run: Any,
    registry: "RuntimeTaskRegistry",
    tool_use_id: str | None = None,
) -> LocalWorkflowTaskState:
    import time

    state = LocalWorkflowTaskState(
        id=task_id,
        type="local_workflow",
        status="running",
        description=description,
        start_time=time.time(),
        output_file=output_file,
        tool_use_id=tool_use_id,
        run_id=run_id,
        workflow_name=workflow_name,
        progress=progress,
        run=run,
        summary=_safe_summary(progress),
    )
    registry.upsert(state)
    return state


def update_workflow_summary(task_id: str, registry: "RuntimeTaskRegistry") -> None:
    """Refresh the cached one-line summary from the live progress snapshot."""

    def _update(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        return replace(prev, summary=_safe_summary(prev.progress))

    registry.update(task_id, _update)


def _terminal_replace(prev: LocalWorkflowTaskState, *, status: str, **extras: Any) -> TaskStateBase:
    import time

    from src.tasks.eviction import PANEL_GRACE_SECONDS, schedule_eviction

    moment = time.time()
    transitioned = replace(prev, status=status, end_time=moment, **extras)
    return schedule_eviction(transitioned, grace_seconds=PANEL_GRACE_SECONDS, now=moment)


def complete_workflow_task(task_id: str, *, result: Any, registry: "RuntimeTaskRegistry") -> None:
    def _complete(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        return _terminal_replace(prev, status="completed", result=result, summary=_safe_summary(prev.progress))

    registry.update(task_id, _complete)


def fail_workflow_task(task_id: str, *, error: str, registry: "RuntimeTaskRegistry") -> None:
    def _fail(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        return _terminal_replace(prev, status="failed", error=error)

    registry.update(task_id, _fail)


def kill_workflow_task(task_id: str, registry: "RuntimeTaskRegistry") -> None:
    """Abort the whole run (cascades to every subagent) and mark it killed."""
    captured_run: Any = None

    def _kill(prev: TaskStateBase) -> TaskStateBase:
        nonlocal captured_run
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        captured_run = prev.run
        return _terminal_replace(prev, status="killed")

    registry.update(task_id, _kill)
    # Abort OUTSIDE the registry lock (the controller fires listeners).
    if captured_run is not None:
        try:
            captured_run.controller.abort("workflow_stopped")
        except Exception:
            logger.exception("failed to abort workflow run %s", task_id)


def skip_workflow_agent(task_id: str, agent_key: str, registry: "RuntimeTaskRegistry") -> bool:
    """Stop one in-flight agent by its call-path key; it resolves to ``None`` in
    the script. Returns whether a live agent was found."""
    state = registry.get(task_id)
    if not isinstance(state, LocalWorkflowTaskState) or state.run is None:
        return False
    try:
        return bool(state.run.abort_agent(agent_key))
    except Exception:
        logger.exception("failed to skip agent %s in workflow %s", agent_key, task_id)
        return False


def retry_workflow_agent(task_id: str, agent_key: str, registry: "RuntimeTaskRegistry") -> bool:
    """Reserved: re-spawning a single agent mid-run needs engine support that
    does not exist yet. Reports unsupported rather than silently no-op'ing."""
    logger.info("retry_workflow_agent is not yet supported (task=%s agent=%s)", task_id, agent_key)
    return False


def _safe_summary(progress: Any) -> str | None:
    try:
        return progress.summary() if progress is not None else None
    except Exception:
        return None


# ── Task adapter ──────────────────────────────────────────────────────────────


class LocalWorkflowTask:
    name: str = "LocalWorkflowTask"
    type: Literal["local_workflow"] = "local_workflow"

    async def kill(self, task_id: str, registry: "RuntimeTaskRegistry") -> None:
        kill_workflow_task(task_id, registry)


__all__ = [
    "LocalWorkflowTaskState",
    "LocalWorkflowTask",
    "is_local_workflow_task",
    "register_workflow_task",
    "update_workflow_summary",
    "complete_workflow_task",
    "fail_workflow_task",
    "kill_workflow_task",
    "skip_workflow_agent",
    "retry_workflow_agent",
]
