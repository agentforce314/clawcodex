"""Human-readable Cron autonomy status output."""

from __future__ import annotations

from pathlib import Path

from .parser import cron_to_human
from .runs import ACTIVE_RUN_STATUSES, CronRun, read_cron_runs
from .tasks import read_all_cron_tasks


def build_autonomy_status(workspace_root: Path, *, deep: bool = False) -> str:
    tasks = read_all_cron_tasks(workspace_root)
    runs = read_cron_runs(workspace_root)
    active = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]

    lines = ["Autonomy status", "", "Cron jobs"]
    if not tasks:
        lines.append("  No scheduled cron jobs.")
    else:
        lines.extend(_job_table(tasks))

    lines.extend(["", "Scheduled-task runs"])
    if not runs:
        lines.append("  No scheduled-task runs.")
    else:
        lines.append(f"  Active: {len(active)}  Total: {len(runs)}")
        selected_runs = runs if deep else runs[:10]
        lines.extend(_run_table(selected_runs, deep=deep))
        if not deep and len(runs) > len(selected_runs):
            lines.append(f"  ... {len(runs) - len(selected_runs)} older runs hidden; use --deep to show all.")
    return "\n".join(lines)


def build_autonomy_runs(workspace_root: Path, *, deep: bool = False) -> str:
    runs = read_cron_runs(workspace_root)
    if not runs:
        return "No scheduled-task runs."
    selected_runs = runs if deep else runs[:20]
    lines = ["Scheduled-task runs", *_run_table(selected_runs, deep=deep)]
    if not deep and len(runs) > len(selected_runs):
        lines.append(f"... {len(runs) - len(selected_runs)} older runs hidden; use --deep to show all.")
    return "\n".join(lines)


def build_schedule_list(workspace_root: Path) -> str:
    tasks = read_all_cron_tasks(workspace_root)
    if not tasks:
        return "No scheduled cron jobs."
    return "\n".join(["Scheduled cron jobs", *_job_table(tasks)])


def _job_table(tasks) -> list[str]:
    lines = [f"  {'ID':<8} {'Schedule':<18} {'Recurring':<9} {'Durable':<7} {'Next':<13} Prompt"]
    for task in tasks:
        prompt = _truncate(task.prompt, 60)
        lines.append(
            f"  {task.id:<8} {_truncate(cron_to_human(task.cron), 18):<18} "
            f"{str(task.recurring):<9} {str(task.durable):<7} {str(task.next_fire_at or '—'):<13} {prompt}"
        )
    return lines


def _run_table(runs: list[CronRun], *, deep: bool = False) -> list[str]:
    if not deep:
        lines = [f"  {'Run ID':<8} {'Task ID':<8} {'Status':<9} {'Queued':<13} Prompt"]
        for run in runs:
            lines.append(
                f"  {run.id:<8} {run.task_id:<8} {run.status:<9} {run.queued_at:<13} {_truncate(run.prompt_preview or run.prompt, 60)}"
            )
        return lines

    lines: list[str] = []
    for run in runs:
        lines.append(f"  Run {run.id}")
        lines.append(f"    status: {run.status}")
        lines.append(f"    task_id: {run.task_id}")
        lines.append(f"    trigger: {run.trigger}")
        lines.append(f"    runtime: {run.runtime}")
        lines.append(f"    source: {run.source_id or run.task_id} ({_truncate(run.source_label or run.prompt, 80)})")
        lines.append(f"    prompt: {_truncate(run.prompt_preview or run.prompt, 120)}")
        lines.append(f"    queued: {run.queued_at}")
        lines.append(f"    started: {run.started_at or '—'}")
        lines.append(f"    ended: {run.ended_at or run.completed_at or '—'}")
        lines.append(f"    root_dir: {run.root_dir or '—'}")
        lines.append(f"    current_dir: {run.current_dir or '—'}")
        lines.append(f"    owner: {run.owner_key} pid={run.owner_process_id or '—'} session={run.owner_session_id or '—'}")
        if run.error:
            lines.append(f"    error: {_truncate(run.error, 160)}")
    return lines


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"
