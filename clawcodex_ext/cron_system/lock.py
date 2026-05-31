"""Filesystem lock for Cron scheduler ownership."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .models import (
    SCHEDULED_TASKS_LOCK_RELATIVE_PATH,
    SCHEDULED_TASKS_STORAGE_LOCK_RELATIVE_PATH,
)

DEFAULT_STALE_LOCK_MS = 10 * 60 * 1000


@dataclass
class CronTaskLock:
    workspace_root: Path
    session_id: str
    stale_after_ms: int = DEFAULT_STALE_LOCK_MS
    lock_relative_path: Path = SCHEDULED_TASKS_LOCK_RELATIVE_PATH
    acquired: bool = False

    @property
    def path(self) -> Path:
        return self.workspace_root / self.lock_relative_path

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": self.session_id,
            "pid": os.getpid(),
            "acquiredAt": int(time.time() * 1000),
        }
        encoded = json.dumps(payload, sort_keys=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not self._recover_if_stale():
                return False
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("sessionId") == self.session_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> CronTaskLock:
        if not self.acquired and not self.acquire():
            raise TimeoutError(f"could not acquire cron lock: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _recover_if_stale(self) -> bool:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except OSError:
            return False
        except json.JSONDecodeError:
            return self._unlink_existing_if_old()

        pid = data.get("pid")
        acquired_at = data.get("acquiredAt")
        now = int(time.time() * 1000)
        age_stale = isinstance(acquired_at, int) and now - acquired_at > self.stale_after_ms
        pid_dead = isinstance(pid, int) and not _pid_is_alive(pid)
        if age_stale or pid_dead:
            return self._unlink_existing()
        return False

    def _unlink_existing(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _unlink_existing_if_old(self) -> bool:
        try:
            stat = self.path.stat()
        except OSError:
            return False
        age_ms = int((time.time() - stat.st_mtime) * 1000)
        if age_ms <= self.stale_after_ms:
            return False
        return self._unlink_existing()


def acquire_cron_storage_lock(workspace_root: Path, session_id: str) -> CronTaskLock:
    deadline = time.monotonic() + 10
    lock = CronTaskLock(
        workspace_root,
        session_id,
        lock_relative_path=SCHEDULED_TASKS_STORAGE_LOCK_RELATIVE_PATH,
    )
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"could not acquire cron storage lock: {lock.path}")
        time.sleep(0.01)
    return lock


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True
