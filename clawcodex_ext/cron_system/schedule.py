"""Local schedule command helpers for Cron tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping

from .models import CronTask
from .parser import cron_to_human
from .runs import CreateCronRunParams, CronRun, create_queued_run, read_cron_runs
from .tasks import read_all_cron_tasks


@dataclass(frozen=True)
class CronTaskDetail:
    id: str
    status: str
    cron: str
    human_schedule: str
    prompt: str
    recurring: bool
    durable: bool
    created_at: int
    updated_at: int
    last_fired_at: int | None
    next_fire_at: int | None
    last_run: CronRun | None


def get_cron_task_detail(
    workspace_root: Path,
    task_id: str,
    session_store: MutableMapping[str, CronTask | dict[str, Any]] | None = None,
) -> CronTaskDetail | None:
    task = _find_cron_task(workspace_root, task_id, session_store)
    if task is None:
        return None
    runs = [run for run in read_cron_runs(workspace_root) if run.task_id == task.id or run.source_id == task.id]
    last_run = runs[0] if runs else None
    return CronTaskDetail(
        id=task.id,
        status="enabled",
        cron=task.cron,
        human_schedule=cron_to_human(task.cron),
        prompt=task.prompt,
        recurring=task.recurring,
        durable=task.durable,
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_fired_at=task.last_fired_at,
        next_fire_at=task.next_fire_at,
        last_run=last_run,
    )


def format_cron_task_detail(detail: CronTaskDetail) -> str:
    lines = [
        f"Trigger: {detail.id}",
        f"Status: {detail.status}",
        f"Schedule: {detail.human_schedule}",
        "Agent: —",
        f"Next run: {_format_optional_ms(detail.next_fire_at)}",
        f"Last run: {_format_last_run(detail)}",
        f"Created: {_format_optional_ms(detail.created_at)}",
        f"Prompt: {detail.prompt}",
        f"Recurring: {detail.recurring}",
        f"Durable: {detail.durable}",
    ]
    if detail.last_run is not None:
        lines.extend(
            [
                f"Last run ID: {detail.last_run.id}",
                f"Last run status: {detail.last_run.status}",
            ]
        )
        if detail.last_run.error:
            lines.append(f"Last run error: {detail.last_run.error}")
    return "\n".join(lines)


def manual_fire_cron_task(
    workspace_root: Path,
    task_id: str,
    session_store: MutableMapping[str, CronTask | dict[str, Any]] | None = None,
    *,
    current_dir: Path | str | None = None,
) -> CronRun | None:
    task = _find_cron_task(workspace_root, task_id, session_store)
    if task is None:
        return None
    return create_queued_run(
        workspace_root,
        CreateCronRunParams(
            task_id=task.id,
            prompt=task.prompt,
            cron=task.cron,
            source_id=task.id,
            source_label=task.prompt,
            root_dir=str(workspace_root),
            current_dir=str(current_dir or workspace_root),
        ),
    )


def format_manual_fire_result(task_id: str, run: CronRun | None) -> str:
    if run is None:
        return f"Trigger {task_id} was not fired because a previous run is still queued or running."
    return f"Trigger {task_id} fired.\nRun ID: {run.id}"


def _find_cron_task(
    workspace_root: Path,
    task_id: str,
    session_store: MutableMapping[str, CronTask | dict[str, Any]] | None = None,
) -> CronTask | None:
    return next((task for task in read_all_cron_tasks(workspace_root, session_store) if task.id == task_id), None)


def _format_last_run(detail: CronTaskDetail) -> str:
    if detail.last_run is not None:
        value = detail.last_run.ended_at or detail.last_run.completed_at or detail.last_run.started_at or detail.last_run.queued_at
        return f"{_format_optional_ms(value)} ({detail.last_run.status})"
    return _format_optional_ms(detail.last_fired_at)


def _format_optional_ms(value: int | None) -> str:
    if value is None or value == 0:
        return "—"
    return str(value)
