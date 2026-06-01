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
STALE_ACTIVE_RUN_ERROR_PREFIX = "Recovered stale active scheduled-task run"
MAX_CRON_RUNS = 200
DEFAULT_RUNTIME = "automatic"
DEFAULT_TRIGGER = "scheduled-task"
DEFAULT_OWNER_KEY = "local"
CronRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
CronRunRuntime = Literal["automatic", "flow_step"]


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
    runtime: CronRunRuntime = DEFAULT_RUNTIME
    trigger: str = DEFAULT_TRIGGER
    source_id: str | None = None
    source_label: str | None = None
    prompt_preview: str | None = None
    root_dir: str | None = None
    current_dir: str | None = None
    owner_key: str = DEFAULT_OWNER_KEY
    owner_process_id: int | None = None
    owner_session_id: str | None = None
    ended_at: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronRun | None:
        try:
            run_id = _first_value(data, "id", "runId", "run_id")
            task_id = _first_value(data, "task_id", "taskId", "sourceId", "source_id")
            prompt = _first_value(data, "prompt", "sourceLabel", "source_label", "promptPreview", "prompt_preview")
            status = data["status"]
            if not isinstance(run_id, str) or not run_id:
                return None
            if not isinstance(task_id, str) or not task_id:
                return None
            if not isinstance(prompt, str) or not prompt.strip():
                return None
            if status not in ACTIVE_RUN_STATUSES and status not in TERMINAL_RUN_STATUSES:
                return None
            queued_at = int(data.get("queued_at") or data.get("queuedAt") or data.get("createdAt") or data.get("created_at") or 0)
            source_id = _optional_str(data.get("source_id", data.get("sourceId"))) or task_id
            source_label = _optional_str(data.get("source_label", data.get("sourceLabel"))) or prompt
            prompt_preview = _optional_str(data.get("prompt_preview", data.get("promptPreview"))) or _truncate_prompt_preview(prompt)
            completed_at = _optional_int(data.get("completed_at", data.get("completedAt", data.get("endedAt", data.get("ended_at")))))
            ended_at = _optional_int(data.get("ended_at", data.get("endedAt"))) or completed_at
            runtime = data.get("runtime") or DEFAULT_RUNTIME
            if runtime not in {"automatic", "flow_step"}:
                runtime = DEFAULT_RUNTIME
            return cls(
                id=run_id,
                task_id=task_id,
                prompt=prompt,
                status=status,
                queued_at=queued_at,
                cron=_optional_str(data.get("cron")),
                started_at=_optional_int(data.get("started_at", data.get("startedAt"))),
                completed_at=completed_at,
                error=_optional_str(data.get("error")),
                runtime=runtime,
                trigger=_optional_str(data.get("trigger")) or DEFAULT_TRIGGER,
                source_id=source_id,
                source_label=source_label,
                prompt_preview=prompt_preview,
                root_dir=_optional_str(data.get("root_dir", data.get("rootDir"))),
                current_dir=_optional_str(data.get("current_dir", data.get("currentDir"))),
                owner_key=_optional_str(data.get("owner_key", data.get("ownerKey"))) or DEFAULT_OWNER_KEY,
                owner_process_id=_optional_int(data.get("owner_process_id", data.get("ownerProcessId"))),
                owner_session_id=_optional_str(data.get("owner_session_id", data.get("ownerSessionId"))),
                ended_at=ended_at,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.id,
            "task_id": self.task_id,
            "prompt": self.prompt,
            "status": self.status,
            "queued_at": self.queued_at,
            "created_at": self.queued_at,
            "cron": self.cron,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "ended_at": self.ended_at or self.completed_at,
            "error": self.error,
            "runtime": self.runtime,
            "trigger": self.trigger,
            "source_id": self.source_id or self.task_id,
            "source_label": self.source_label or self.prompt,
            "prompt_preview": self.prompt_preview or _truncate_prompt_preview(self.prompt),
            "root_dir": self.root_dir,
            "current_dir": self.current_dir,
            "owner_key": self.owner_key,
            "owner_process_id": self.owner_process_id,
            "owner_session_id": self.owner_session_id,
        }


@dataclass(frozen=True)
class CreateCronRunParams:
    task_id: str
    prompt: str
    cron: str | None = None
    queued_at: int | None = None
    runtime: CronRunRuntime = DEFAULT_RUNTIME
    trigger: str = DEFAULT_TRIGGER
    source_id: str | None = None
    source_label: str | None = None
    root_dir: str | None = None
    current_dir: str | None = None
    owner_key: str = DEFAULT_OWNER_KEY
    owner_process_id: int | None = None
    owner_session_id: str | None = None


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
    sorted_runs = sorted(runs, key=lambda item: (item.queued_at, item.id), reverse=True)[:MAX_CRON_RUNS]
    payload = {
        "version": 2,
        "runs": [run.to_dict() for run in sorted_runs],
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_active_run_for_task(workspace_root: Path, task_id: str) -> CronRun | None:
    for run in read_cron_runs(workspace_root):
        if run.task_id == task_id and run.status in ACTIVE_RUN_STATUSES:
            return run
    return None


def get_active_run_for_source(
    workspace_root: Path,
    *,
    trigger: str,
    source_id: str,
    owner_key: str | None = None,
) -> CronRun | None:
    for run in read_cron_runs(workspace_root):
        if not _matches_active_source(run, trigger=trigger, source_id=source_id, owner_key=owner_key):
            continue
        if _is_stale_active_run(run):
            update_cron_run_status(
                workspace_root,
                run.id,
                "failed",
                error=f"{STALE_ACTIVE_RUN_ERROR_PREFIX}: owner process {run.owner_process_id} is no longer running.",
            )
            continue
        return run
    return None


def create_queued_run(
    workspace_root: Path,
    params: CreateCronRunParams,
) -> CronRun | None:
    timestamp = params.queued_at if params.queued_at is not None else now_ms()
    source_id = params.source_id or params.task_id
    owner_key = params.owner_key or DEFAULT_OWNER_KEY
    with acquire_cron_storage_lock(workspace_root, f"queue-run-{source_id}"):
        runs = read_cron_runs(workspace_root)
        updated_runs: list[CronRun] = []
        for run in runs:
            if not _matches_active_source(run, trigger=params.trigger, source_id=source_id, owner_key=owner_key):
                updated_runs.append(run)
                continue
            if _is_stale_active_run(run):
                updated_runs.append(
                    replace(
                        run,
                        status="failed",
                        completed_at=timestamp,
                        ended_at=timestamp,
                        error=f"{STALE_ACTIVE_RUN_ERROR_PREFIX}: owner process {run.owner_process_id} is no longer running.",
                    )
                )
                continue
            return None
        run = CronRun(
            id=uuid.uuid4().hex[:8],
            task_id=params.task_id,
            prompt=params.prompt,
            cron=params.cron,
            status="queued",
            queued_at=timestamp,
            runtime=params.runtime,
            trigger=params.trigger,
            source_id=source_id,
            source_label=params.source_label or params.prompt,
            prompt_preview=_truncate_prompt_preview(params.prompt),
            root_dir=params.root_dir or str(workspace_root),
            current_dir=params.current_dir or str(workspace_root),
            owner_key=owner_key,
            owner_process_id=params.owner_process_id,
            owner_session_id=params.owner_session_id,
        )
        updated_runs.append(run)
        write_cron_runs(workspace_root, updated_runs)
        return run


def create_queued_run_for_task(
    workspace_root: Path,
    task: CronTask,
    *,
    queued_at: int | None = None,
    current_dir: Path | str | None = None,
    owner_session_id: str | None = None,
) -> CronRun | None:
    return create_queued_run(
        workspace_root,
        CreateCronRunParams(
            task_id=task.id,
            prompt=task.prompt,
            cron=task.cron,
            queued_at=queued_at,
            source_id=task.id,
            source_label=task.prompt,
            root_dir=str(workspace_root),
            current_dir=str(current_dir or workspace_root),
            owner_process_id=os.getpid(),
            owner_session_id=owner_session_id,
        ),
    )


def claim_cron_run(
    workspace_root: Path,
    run_id: str,
    *,
    timestamp: int | None = None,
) -> CronRun | None:
    updated_at = timestamp if timestamp is not None else now_ms()
    with acquire_cron_storage_lock(workspace_root, f"claim-run-{run_id}"):
        runs = read_cron_runs(workspace_root)
        updated: list[CronRun] = []
        changed: CronRun | None = None
        for run in runs:
            if run.id != run_id:
                updated.append(run)
                continue
            if run.status != "queued":
                updated.append(run)
                continue
            changed = replace(run, status="running", started_at=run.started_at or updated_at)
            updated.append(changed)
        if changed is not None:
            write_cron_runs(workspace_root, updated)
        return changed


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
                next_run = replace(next_run, completed_at=updated_at, ended_at=updated_at)
            updated.append(next_run)
            changed = next_run
        if changed is not None:
            write_cron_runs(workspace_root, updated)
        return changed


def finalize_cron_run(
    workspace_root: Path,
    run_id: str,
    status: Literal["completed", "failed", "cancelled"],
    *,
    timestamp: int | None = None,
    error: str | None = None,
) -> CronRun | None:
    return update_cron_run_status(workspace_root, run_id, status, timestamp=timestamp, error=error)


def _matches_active_source(
    run: CronRun,
    *,
    trigger: str,
    source_id: str,
    owner_key: str | None = None,
) -> bool:
    return (
        run.trigger == trigger
        and (run.source_id or run.task_id) == source_id
        and (owner_key is None or run.owner_key == owner_key)
        and run.status in ACTIVE_RUN_STATUSES
    )


def _is_stale_active_run(run: CronRun) -> bool:
    if run.status not in ACTIVE_RUN_STATUSES or run.owner_process_id is None:
        return False
    if run.owner_process_id == os.getpid():
        return False
    try:
        os.kill(run.owner_process_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _truncate_prompt_preview(prompt: str, max_length: int = 80) -> str:
    normalized = " ".join(prompt.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1] + "…"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value
