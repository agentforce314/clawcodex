"""Scheduled-task run records for Cron execution status."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Literal

from .lock import acquire_cron_storage_lock
from .models import CronTask

RUNS_RELATIVE_PATH = Path(".claude/scheduled_task_runs.json")
ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
CronRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class CronRun:
    id: str
    task_id: str
    prompt: str
    status: CronRunStatus
    queued_at: int
    cron: str | None = None
    started_at: int | None = None
    completed_at: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronRun | None:
        try:
            run_id = data["id"]
            task_id = data["task_id"] if "task_id" in data else data["taskId"]
            prompt = data["prompt"]
            status = data["status"]
            if not isinstance(run_id, str) or not run_id:
                return None
            if not isinstance(task_id, str) or not task_id:
                return None
            if not isinstance(prompt, str) or not prompt.strip():
                return None
            if status not in ACTIVE_RUN_STATUSES and status not in TERMINAL_RUN_STATUSES:
                return None
            return cls(
                id=run_id,
                task_id=task_id,
                prompt=prompt,
                status=status,
                queued_at=int(data.get("queued_at") or data.get("queuedAt") or 0),
                cron=_optional_str(data.get("cron")),
                started_at=_optional_int(data.get("started_at", data.get("startedAt"))),
                completed_at=_optional_int(data.get("completed_at", data.get("completedAt"))),
                error=_optional_str(data.get("error")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "prompt": self.prompt,
            "status": self.status,
            "queued_at": self.queued_at,
            "cron": self.cron,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


def runs_file_path(workspace_root: Path) -> Path:
    return workspace_root / RUNS_RELATIVE_PATH


def now_ms() -> int:
    return int(time.time() * 1000)


def read_cron_runs(workspace_root: Path) -> list[CronRun]:
    path = runs_file_path(workspace_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    entries = raw.get("runs", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []

    runs: list[CronRun] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        run = CronRun.from_dict(entry)
        if run is not None:
            runs.append(run)
    runs.sort(key=lambda run: (run.queued_at, run.id), reverse=True)
    return runs


def write_cron_runs(workspace_root: Path, runs: Iterable[CronRun]) -> None:
    path = runs_file_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "runs": [run.to_dict() for run in sorted(runs, key=lambda item: (item.queued_at, item.id), reverse=True)],
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_active_run_for_task(workspace_root: Path, task_id: str) -> CronRun | None:
    for run in read_cron_runs(workspace_root):
        if run.task_id == task_id and run.status in ACTIVE_RUN_STATUSES:
            return run
    return None


def create_queued_run_for_task(
    workspace_root: Path,
    task: CronTask,
    *,
    queued_at: int | None = None,
) -> CronRun | None:
    timestamp = queued_at if queued_at is not None else now_ms()
    with acquire_cron_storage_lock(workspace_root, f"queue-run-{task.id}"):
        runs = read_cron_runs(workspace_root)
        for run in runs:
            if run.task_id == task.id and run.status in ACTIVE_RUN_STATUSES:
                return None
        run = CronRun(
            id=uuid.uuid4().hex[:8],
            task_id=task.id,
            prompt=task.prompt,
            cron=task.cron,
            status="queued",
            queued_at=timestamp,
        )
        runs.append(run)
        write_cron_runs(workspace_root, runs)
        return run


def update_cron_run_status(
    workspace_root: Path,
    run_id: str,
    status: CronRunStatus,
    *,
    timestamp: int | None = None,
    error: str | None = None,
) -> CronRun | None:
    if status not in ACTIVE_RUN_STATUSES and status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"invalid cron run status: {status}")
    updated_at = timestamp if timestamp is not None else now_ms()
    with acquire_cron_storage_lock(workspace_root, f"update-run-{run_id}"):
        runs = read_cron_runs(workspace_root)
        updated: list[CronRun] = []
        changed: CronRun | None = None
        for run in runs:
            if run.id != run_id:
                updated.append(run)
                continue
            next_run = replace(run, status=status, error=error)
            if status == "running" and next_run.started_at is None:
                next_run = replace(next_run, started_at=updated_at)
            if status in TERMINAL_RUN_STATUSES:
                next_run = replace(next_run, completed_at=updated_at)
            updated.append(next_run)
            changed = next_run
        if changed is not None:
            write_cron_runs(workspace_root, updated)
        return changed


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value
