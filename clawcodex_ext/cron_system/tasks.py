"""File-backed Cron task storage."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, MutableMapping

from .jitter import jittered_next_cron_run_ms
from .lock import acquire_cron_storage_lock
from .models import (
    DEFAULT_RECURRING_MAX_AGE_MS,
    SCHEDULED_TASKS_RELATIVE_PATH,
    CronJitterConfig,
    CronTask,
    validate_jitter_config,
)
from .parser import parse_cron_expression


def tasks_file_path(workspace_root: Path) -> Path:
    return workspace_root / SCHEDULED_TASKS_RELATIVE_PATH


def now_ms() -> int:
    return int(time.time() * 1000)


def read_cron_tasks(workspace_root: Path) -> list[CronTask]:
    path = tasks_file_path(workspace_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    entries = raw.get("tasks", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []

    tasks: list[CronTask] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        task = CronTask.from_dict(entry)
        if task is not None and parse_cron_expression(task.cron) is not None:
            tasks.append(task)
    tasks.sort(key=lambda task: task.id)
    return tasks


def write_cron_tasks(workspace_root: Path, tasks: Iterable[CronTask]) -> None:
    path = tasks_file_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "tasks": [task.to_dict() for task in sorted(tasks, key=lambda item: item.id)],
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def read_session_cron_tasks(session_store: MutableMapping[str, CronTask | dict] | None) -> list[CronTask]:
    if session_store is None:
        return []

    tasks: list[CronTask] = []
    stale_ids: list[str] = []
    for task_id, value in session_store.items():
        if isinstance(value, CronTask):
            task = value
        elif isinstance(value, dict):
            task = CronTask.from_dict(value)
        else:
            task = None
        if task is None or parse_cron_expression(task.cron) is None:
            stale_ids.append(task_id)
            continue
        tasks.append(task)

    for task_id in stale_ids:
        session_store.pop(task_id, None)

    tasks.sort(key=lambda task: task.id)
    return tasks


def write_session_cron_tasks(session_store: MutableMapping[str, CronTask | dict], tasks: Iterable[CronTask]) -> None:
    session_store.clear()
    for task in sorted(tasks, key=lambda item: item.id):
        session_store[task.id] = task


def read_all_cron_tasks(
    workspace_root: Path,
    session_store: MutableMapping[str, CronTask | dict] | None = None,
) -> list[CronTask]:
    tasks = [*read_session_cron_tasks(session_store), *read_cron_tasks(workspace_root)]
    tasks.sort(key=lambda task: task.id)
    return tasks


def has_cron_tasks_sync(workspace_root: Path) -> bool:
    return bool(read_cron_tasks(workspace_root))


def add_cron_task(
    workspace_root: Path,
    *,
    cron: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
    jitter: CronJitterConfig | None = None,
    created_at: int | None = None,
    session_store: MutableMapping[str, CronTask | dict] | None = None,
) -> CronTask:
    fields = parse_cron_expression(cron)
    if fields is None:
        raise ValueError("cron must be a valid five-field expression")

    timestamp = created_at if created_at is not None else now_ms()
    task_id = uuid.uuid4().hex[:8]
    task = CronTask(
        id=task_id,
        cron=cron,
        prompt=prompt,
        recurring=recurring,
        durable=durable,
        created_at=timestamp,
        updated_at=timestamp,
        expires_at=timestamp + DEFAULT_RECURRING_MAX_AGE_MS if recurring else None,
        jitter=jitter or CronJitterConfig(),
    )
    task = replace(
        task,
        next_fire_at=jittered_next_cron_run_ms(
            task.id,
            fields,
            _datetime_from_ms(timestamp),
            task.jitter,
        ),
    )
    if not durable:
        if session_store is None:
            raise ValueError("session_store is required for session cron tasks")
        tasks = read_session_cron_tasks(session_store)
        tasks.append(task)
        write_session_cron_tasks(session_store, tasks)
        return task

    with acquire_cron_storage_lock(workspace_root, f"add-{task_id}"):
        tasks = read_cron_tasks(workspace_root)
        tasks.append(task)
        write_cron_tasks(workspace_root, tasks)
    return task


def write_permanent_task_if_missing(
    workspace_root: Path,
    *,
    cron: str,
    prompt: str,
    recurring: bool = True,
    jitter: CronJitterConfig | None = None,
    created_at: int | None = None,
    task_id: str | None = None,
) -> tuple[CronTask, bool]:
    """F-22-G4 installer entry point.

    Idempotent: writes the task only if no existing task matches the
    ``cron`` expression AND ``prompt`` (case-insensitive trimmed match).
    Returns ``(task, created)`` so the installer can log "skipped" on
    re-install.

    Raises ``PermissionError`` if any pre-existing task has
    ``permanent=True`` but a different ``cron`` or ``prompt`` — this guards
    against the installer accidentally overwriting a system task.
    """
    fields = parse_cron_expression(cron)
    if fields is None:
        raise ValueError("cron must be a valid five-field expression")

    normalized_cron = cron.strip()
    normalized_prompt = prompt.strip()
    with acquire_cron_storage_lock(workspace_root, f"permanent-{task_id or 'install'}"):
        existing = read_cron_tasks(workspace_root)
        for task in existing:
            if task.permanent:
                same_schedule = task.cron.strip() == normalized_cron
                same_prompt = task.prompt.strip() == normalized_prompt
                if same_schedule and same_prompt:
                    return task, False
                if not (same_schedule and same_prompt):
                    raise PermissionError(
                        f"refusing to overwrite permanent task {task.id!r} "
                        f"(cron={task.cron!r}, prompt={task.prompt[:40]!r})"
                    )

        # If a non-permanent task with the same shape exists, replace it
        # with the permanent version. Installers should converge to a
        # permanent record on first run.
        filtered = [
            t
            for t in existing
            if not (
                t.cron.strip() == normalized_cron
                and t.prompt.strip() == normalized_prompt
            )
        ]
        timestamp = created_at if created_at is not None else now_ms()
        new_id = task_id or uuid.uuid4().hex[:8]
        permanent_task = CronTask(
            id=new_id,
            cron=normalized_cron,
            prompt=normalized_prompt,
            recurring=recurring,
            durable=True,
            created_at=timestamp,
            updated_at=timestamp,
            # Permanent tasks intentionally never auto-expire.
            expires_at=None,
            jitter=validate_jitter_config(jitter) if jitter is not None else None,
            permanent=True,
        )
        # Compute next_fire_at with the (recurring) jitter path.
        from .jitter import jittered_next_cron_run_ms

        permanent_task = replace(
            permanent_task,
            next_fire_at=jittered_next_cron_run_ms(
                permanent_task.id,
                fields,
                _datetime_from_ms(timestamp),
                permanent_task.jitter,
            ),
        )
        filtered.append(permanent_task)
        write_cron_tasks(workspace_root, filtered)
        return permanent_task, True


def remove_cron_tasks(
    workspace_root: Path,
    task_id: str,
    session_store: MutableMapping[str, CronTask | dict] | None = None,
) -> bool:
    if session_store is not None and task_id in session_store:
        session_store.pop(task_id, None)
        return True

    with acquire_cron_storage_lock(workspace_root, f"remove-{task_id}"):
        tasks = read_cron_tasks(workspace_root)
        remaining = [task for task in tasks if task.id != task_id]
        if len(remaining) == len(tasks):
            return False
        write_cron_tasks(workspace_root, remaining)
    return True


def mark_cron_tasks_fired(
    workspace_root: Path,
    fired: Iterable[CronTask],
    fired_at: int | None = None,
) -> list[CronTask]:
    timestamp = fired_at if fired_at is not None else now_ms()
    fired_by_id = {task.id: task for task in fired}
    with acquire_cron_storage_lock(workspace_root, f"mark-fired-{uuid.uuid4().hex}"):
        current = read_cron_tasks(workspace_root)
        updated: list[CronTask] = []
        result: list[CronTask] = []
        for task in current:
            if task.id not in fired_by_id:
                updated.append(task)
                continue
            if not task.recurring:
                result.append(task)
                continue
            fields = parse_cron_expression(task.cron)
            if fields is None:
                next_fire_at = None
            else:
                next_fire_at = jittered_next_cron_run_ms(
                    task.id,
                    fields,
                    _datetime_from_ms(timestamp),
                    task.jitter,
                )
            new_task = replace(
                task,
                last_fired_at=timestamp,
                next_fire_at=next_fire_at,
                updated_at=timestamp,
            )
            updated.append(new_task)
            result.append(new_task)
        write_cron_tasks(workspace_root, updated)
    return result


def find_due_tasks(workspace_root: Path, at_ms: int | None = None) -> list[CronTask]:
    timestamp = at_ms if at_ms is not None else now_ms()
    return [
        task
        for task in read_cron_tasks(workspace_root)
        if task.next_fire_at is not None and task.next_fire_at <= timestamp
    ]


def find_missed_tasks(workspace_root: Path, at_ms: int | None = None) -> list[CronTask]:
    timestamp = at_ms if at_ms is not None else now_ms()
    return [
        task
        for task in read_cron_tasks(workspace_root)
        if not task.recurring and task.next_fire_at is not None and task.next_fire_at < timestamp
    ]


def remove_missed_tasks(workspace_root: Path, missed: Iterable[CronTask]) -> None:
    missed_ids = {task.id for task in missed}
    if not missed_ids:
        return
    with acquire_cron_storage_lock(workspace_root, f"remove-missed-{uuid.uuid4().hex}"):
        tasks = read_cron_tasks(workspace_root)
        remaining = [task for task in tasks if task.id not in missed_ids]
        if len(remaining) != len(tasks):
            write_cron_tasks(workspace_root, remaining)


def prune_expired_recurring_tasks(
    workspace_root: Path,
    at_ms: int | None = None,
    *,
    max_age_ms: int | None = None,
) -> list[CronTask]:
    """Remove recurring tasks past their expiry.

    ``max_age_ms`` (F-22-G2) lets the scheduler pass a live config value
    so an operator tightening ``recurringMaxAgeMs`` mid-session prunes
    stale tasks immediately. When None, the per-task ``expires_at`` is
    used (the value baked at creation time). ``max_age_ms == 0`` disables
    age-based pruning entirely (matches CCB recurringMaxAgeMs=0).
    """
    timestamp = at_ms if at_ms is not None else now_ms()
    with acquire_cron_storage_lock(workspace_root, f"prune-{uuid.uuid4().hex}"):
        tasks = read_cron_tasks(workspace_root)
        # F-22-G4: permanent tasks are exempt from auto-expiry. The
        # assistant-mode installer writes them directly; they must survive
        # restart loops (write_if_missing skips existing files, so a
        # deleted permanent task cannot be recreated).
        def _is_kept(task: CronTask) -> bool:
            if task.permanent or not task.recurring:
                return True
            if max_age_ms is not None:
                if max_age_ms == 0:
                    return True
                return task.created_at + max_age_ms > timestamp
            if task.expires_at is None:
                return True
            return task.expires_at > timestamp
        kept = [task for task in tasks if _is_kept(task)]
        removed = [task for task in tasks if task not in kept]
        if removed:
            write_cron_tasks(workspace_root, kept)
    return removed


def _datetime_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000)
