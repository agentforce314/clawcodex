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
        lines.extend(_run_table(selected_runs))
        if not deep and len(runs) > len(selected_runs):
            lines.append(f"  ... {len(runs) - len(selected_runs)} older runs hidden; use --deep to show all.")
    return "\n".join(lines)


def build_autonomy_runs(workspace_root: Path, *, deep: bool = False) -> str:
    runs = read_cron_runs(workspace_root)
    if not runs:
        return "No scheduled-task runs."
    selected_runs = runs if deep else runs[:20]
    lines = ["Scheduled-task runs", *_run_table(selected_runs)]
    if not deep and len(runs) > len(selected_runs):
        lines.append(f"... {len(runs) - len(selected_runs)} older runs hidden; use --deep to show all.")
    return "\n".join(lines)


def _job_table(tasks) -> list[str]:
    lines = [f"  {'ID':<8} {'Schedule':<18} {'Recurring':<9} {'Durable':<7} Prompt"]
    for task in tasks:
        prompt = _truncate(task.prompt, 60)
        lines.append(
            f"  {task.id:<8} {_truncate(cron_to_human(task.cron), 18):<18} "
            f"{str(task.recurring):<9} {str(task.durable):<7} {prompt}"
        )
    return lines


def _run_table(runs: list[CronRun]) -> list[str]:
    lines = [f"  {'Run ID':<8} {'Task ID':<8} {'Status':<9} {'Queued':<13} Prompt"]
    for run in runs:
        lines.append(
            f"  {run.id:<8} {run.task_id:<8} {run.status:<9} {run.queued_at:<13} {_truncate(run.prompt, 60)}"
        )
    return lines


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"
