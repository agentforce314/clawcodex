"""Background launcher that ties a workflow run to a ``local_workflow`` task.

``run_workflow_task`` is the awaitable the Workflow tool schedules: it runs the
engine, registers the background task (via the engine's ``on_start`` hook with
the live ``WorkflowRun`` so the task can abort it), keeps the task's summary
fresh, and records the terminal state. It is unit-testable end-to-end with a
fake ``AgentRunner`` and a plain ``RuntimeTaskRegistry`` — no live model needed.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from src.utils.abort_controller import AbortController, create_abort_controller

from .errors import WorkflowMetaError
from .journal import Journal, JournalRecord, records_to_json
from .progress import WorkflowProgress
from .types import AgentRunner

logger = logging.getLogger(__name__)


def persist_journal(path: str, records: Mapping) -> None:
    """Write a run's journal to disk (best-effort) so it can resume in-session."""
    temp_path: Path | None = None
    try:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=file.parent, encoding="utf-8", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(records_to_json(records))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, file)
    except OSError as exc:
        logger.debug("could not persist workflow journal to %s: %s", path, exc)
    finally:
        # Close before unlinking: Windows cannot remove an open temp file.
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove journal temp file %s", temp_path)


def load_journal(path: str) -> Optional[dict]:
    """Load a prior run's journal from disk, or None if absent/unreadable."""
    try:
        return Journal.load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("could not load workflow journal from %s: %s", path, exc)
        return None


async def run_workflow_task(
    *,
    source: str,
    runner: AgentRunner,
    registry: Any,  # RuntimeTaskRegistry
    task_id: str,
    run_id: str,
    output_file: str,
    args: Any = None,
    controller: Optional[AbortController] = None,
    resume: Optional[Mapping] = None,
    resolve_workflow: Optional[Any] = None,
    tool_use_id: Optional[str] = None,
    notification_recipient: str | None = None,
    budget_total: Optional[int] = None,
    max_concurrent: Optional[int] = None,
):
    from src.tasks.local_workflow import (
        complete_workflow_task,
        fail_workflow_task,
        register_workflow_task,
        update_workflow_summary,
    )

    from .runtime import run_workflow

    controller = controller if controller is not None else create_abort_controller()

    def _on_start(run) -> None:
        register_workflow_task(
            task_id=task_id,
            run_id=run_id,
            workflow_name=run.meta.name,
            description=run.meta.description,
            output_file=output_file,
            progress=run.progress,
            run=run,
            registry=registry,
            tool_use_id=tool_use_id,
            notification_recipient=notification_recipient,
        )

    def _on_progress(_progress: WorkflowProgress) -> None:
        update_workflow_summary(task_id, registry)

    try:
        result = await run_workflow(
            source,
            runner=runner,
            args=args,
            run_id=run_id,
            controller=controller,
            on_start=_on_start,
            on_progress=_on_progress,
            resume=resume,
            resolve_workflow=resolve_workflow,
            budget_total=budget_total,
            max_concurrent=max_concurrent,
            on_journal=lambda records: persist_journal(output_file, records),
        )
    except WorkflowMetaError as exc:
        # meta failed before the task was registered — surface a failed task so
        # the user sees what happened rather than a silent no-op.
        register_workflow_task(
            task_id=task_id,
            run_id=run_id,
            workflow_name="workflow",
            description="workflow (invalid meta)",
            output_file=output_file,
            progress=WorkflowProgress(),
            run=None,
            registry=registry,
            tool_use_id=tool_use_id,
            notification_recipient=notification_recipient,
        )
        fail_workflow_task(task_id, error=f"WorkflowMetaError: {exc}", registry=registry)
        return None

    # Persist the journal so a same-session resume can replay completed agents.
    persist_journal(output_file, result.journal)

    if result.ok:
        complete_workflow_task(task_id, result=result.value, registry=registry)
    else:
        fail_workflow_task(task_id, error=result.error or "unknown error", registry=registry)
    return result
