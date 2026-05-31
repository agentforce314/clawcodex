"""Cron scheduler lifecycle independent of frontends."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .lock import CronTaskLock
from .models import CronTask
from .notifications import build_missed_task_notification
from .tasks import (
    find_due_tasks,
    find_missed_tasks,
    mark_cron_tasks_fired,
    now_ms,
    prune_expired_recurring_tasks,
    read_cron_tasks,
    remove_missed_tasks,
)


@dataclass
class CronScheduler:
    workspace_root: Path
    on_fire: Callable[[str], None]
    on_fire_task: Callable[[CronTask], None] | None = None
    on_missed: Callable[[list[CronTask], str], None] | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    check_interval_seconds: float = 1.0

    _thread: threading.Thread | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: CronTaskLock | None = field(default=None, init=False)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        lock = CronTaskLock(self.workspace_root, self.session_id)
        if not lock.acquire():
            return False
        self._lock = lock
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="clawcodex-cron-scheduler", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._lock:
            self._lock.release()
            self._lock = None

    def load(self) -> list[CronTask]:
        return read_cron_tasks(self.workspace_root)

    def check_once(self, at_ms: int | None = None) -> list[CronTask]:
        timestamp = at_ms if at_ms is not None else now_ms()
        prune_expired_recurring_tasks(self.workspace_root, timestamp)
        due = find_due_tasks(self.workspace_root, timestamp)
        if not due:
            return []
        for task in due:
            if self.on_fire_task is not None:
                self.on_fire_task(task)
            else:
                self.on_fire(task.prompt)
        mark_cron_tasks_fired(self.workspace_root, due, timestamp)
        return due

    def notify_missed_once(self, at_ms: int | None = None) -> list[CronTask]:
        missed = find_missed_tasks(self.workspace_root, at_ms)
        if missed:
            remove_missed_tasks(self.workspace_root, missed)
            if self.on_missed is not None:
                self.on_missed(missed, build_missed_task_notification(missed))
        return missed

    def get_next_fire_time(self) -> int | None:
        values = [task.next_fire_at for task in read_cron_tasks(self.workspace_root) if task.next_fire_at is not None]
        return min(values) if values else None

    def _run_loop(self) -> None:
        self.notify_missed_once()
        while not self._stop_event.is_set():
            self.check_once()
            self._stop_event.wait(self.check_interval_seconds)
