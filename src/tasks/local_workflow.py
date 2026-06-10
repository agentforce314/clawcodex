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

import json
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
    enqueue_workflow_notification(task_id, registry, status="completed")


def fail_workflow_task(task_id: str, *, error: str, registry: "RuntimeTaskRegistry") -> None:
    def _fail(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        return _terminal_replace(prev, status="failed", error=error)

    registry.update(task_id, _fail)
    enqueue_workflow_notification(task_id, registry, status="failed", error=error)


def kill_workflow_task(task_id: str, registry: "RuntimeTaskRegistry") -> None:
    """Abort the whole run (cascades to every subagent) and mark it killed."""
    captured_run: Any = None
    fired = False

    def _kill(prev: TaskStateBase) -> TaskStateBase:
        nonlocal captured_run, fired
        if not isinstance(prev, LocalWorkflowTaskState) or is_terminal_task_status(prev.status):
            return prev
        captured_run = prev.run
        fired = True
        return _terminal_replace(prev, status="killed")

    registry.update(task_id, _kill)
    # Abort OUTSIDE the registry lock (the controller fires listeners).
    if captured_run is not None:
        try:
            captured_run.controller.abort("workflow_stopped")
        except Exception:
            logger.exception("failed to abort workflow run %s", task_id)
    if fired:
        enqueue_workflow_notification(task_id, registry, status="killed")


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
    """Re-spawn one in-flight agent by its call-path key (the `r` action).
    Returns whether a live agent was found to retry."""
    state = registry.get(task_id)
    if not isinstance(state, LocalWorkflowTaskState) or state.run is None:
        return False
    try:
        return bool(state.run.retry_agent(agent_key))
    except Exception:
        logger.exception("failed to retry agent %s in workflow %s", agent_key, task_id)
        return False


def _safe_summary(progress: Any) -> str | None:
    try:
        return progress.summary() if progress is not None else None
    except Exception:
        return None


def _render_result(result: Any) -> str | None:
    """Render a workflow's return value for the <result> section (capped)."""
    if result is None:
        return None
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except Exception:
            text = str(result)
    return text[:4000]


def enqueue_workflow_notification(
    task_id: str,
    registry: "RuntimeTaskRegistry",
    *,
    status: str,
    error: str | None = None,
) -> bool:
    """Deliver a workflow's terminal result to the model via the shared
    ``<task-notification>`` queue (mirrors ``enqueue_agent_notification``).

    The ``notified`` flag is check-and-set atomically, so exactly one envelope
    is delivered even if complete/fail/kill race."""
    from src.utils.message_queue_manager import enqueue_pending_notification
    from src.utils.task_notification import build_task_notification_xml

    captured: dict[str, Any] = {}
    should_enqueue = False

    def _mark(prev: TaskStateBase) -> TaskStateBase:
        nonlocal should_enqueue
        if not isinstance(prev, LocalWorkflowTaskState) or prev.notified:
            return prev
        should_enqueue = True
        captured.update(
            name=prev.workflow_name or "workflow",
            output_file=prev.output_file,
            result=prev.result,
            tool_use_id=prev.tool_use_id,
            tokens=(prev.progress.token_total if prev.progress is not None else 0),
        )
        return replace(prev, notified=True)

    registry.update(task_id, _mark)
    if not should_enqueue:
        return False

    final_message = _render_result(captured["result"]) if status == "completed" else None
    xml = build_task_notification_xml(
        task_id=task_id,
        description=captured["name"],
        status=status,  # type: ignore[arg-type]
        output_file=captured["output_file"],
        error=error,
        final_message=final_message,
        usage={"total_tokens": int(captured.get("tokens") or 0), "tool_uses": 0, "duration_ms": 0},
        tool_use_id=captured["tool_use_id"],
    )
    enqueue_pending_notification(value=xml, mode="task-notification")
    return True


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
    "enqueue_workflow_notification",
]
