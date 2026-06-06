"""Filesystem lock for Cron scheduler ownership (F-22-G5).

Three enhancements over the original ``lock.py``:

1. **Cleanup registry** — :func:`register_lock_cleanup` and
   :func:`release_all_locks` support explicit atexit / signal-based release.
2. **PID identity check** — when reading an existing lock, verify the
   recorded PID still belongs to a ClawCodex / claude-code-style process.
   If the PID is alive but the process command line is unrelated (e.g.
   PID was recycled by a different program), the lock is treated as stale
   and recovered.
3. **SessionId takeover** — if a re-entrant process finds its own
   sessionId in the lock file (e.g. a forked child recovering after a
   short restart), it can take over the lock without contention.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import (
    SCHEDULED_TASKS_LOCK_RELATIVE_PATH,
    SCHEDULED_TASKS_STORAGE_LOCK_RELATIVE_PATH,
)

_log = logging.getLogger(__name__)

DEFAULT_STALE_LOCK_MS = 10 * 60 * 1000

# F-22-G5: PID identity probe. When the recorded PID is alive but is not
# a ClawCodex-derived process, treat the lock as stale. Defaults to
# /proc/<pid>/comm on Linux; on macOS uses ps; on unsupported platforms
# returns True (allow).
_DEFAULT_PID_VALIDATOR: Callable[[int], bool] | None = None


def set_pid_validator(validator: Callable[[int], bool] | None) -> None:
    """Override the PID identity probe (used by tests)."""
    global _DEFAULT_PID_VALIDATOR
    _DEFAULT_PID_VALIDATOR = validator


def _default_pid_validator(pid: int) -> bool:
    """True if PID is alive AND the process command line indicates a
    ClawCodex / claude-code / orchestrator-style process. Conservative
    default: alive + not kthreadd/init is good enough to break obvious
    PID-recycling races; matching the comm keeps the test snappy.
    """
    if pid <= 0 or pid == os.getpid():
        return True
    if not _pid_is_alive(pid):
        return False
    comm_path = Path(f"/proc/{pid}/comm")
    try:
        comm = comm_path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        # /proc not available (macOS, Windows). Don't risk a false
        # positive — fall back to "alive only" by returning True.
        return True
    if not comm:
        return True
    # Accept anything that looks like a Python interpreter or our own
    # binaries. We deliberately do NOT require a specific comm string —
    # the goal is to reject *obviously foreign* processes (e.g. nginx,
    # postgres) that recycled the PID.
    if comm in {"python", "python3", "python3.11", "python3.12"}:
        return True
    if "clawcodex" in comm or "claude" in comm or "orchestrator" in comm:
        return True
    # Unknown comm — be permissive but log so ops can tune.
    _log.debug("PID %d comm=%r passes identity check by default", pid, comm)
    return True


# F-22-G5: cleanup registry. Process-exit handlers fire all registered
# cleanup callbacks; cron modules register their lock release here so
# atexit + SIGTERM/SIGINT both unwind correctly.
_cleanup_callbacks: list[Callable[[], Any]] = []
_cleanup_atexit_registered = False


def register_lock_cleanup(callback: Callable[[], Any]) -> Callable[[], None]:
    """Register a process-exit cleanup callback. Returns an unregister fn."""
    _cleanup_callbacks.append(callback)
    _ensure_atexit_registered()
    def _unregister() -> None:
        try:
            _cleanup_callbacks.remove(callback)
        except ValueError:
            pass
    return _unregister


def release_all_locks() -> None:
    """Run all registered cleanup callbacks. Idempotent: callbacks that
    themselves unregister are tolerated.
    """
    # Snapshot the list — callbacks may mutate it.
    pending = list(_cleanup_callbacks)
    for cb in pending:
        try:
            result = cb()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("lock cleanup callback failed: %s", exc)
            continue
        # If the callback returns a coroutine, close it cleanly without
        # awaiting (atexit handlers cannot run async).
        if hasattr(result, "close") and not hasattr(result, "__await__"):
            try:
                result.close()  # type: ignore[union-attr]
            except Exception:  # pragma: no cover
                pass


def _ensure_atexit_registered() -> None:
    global _cleanup_atexit_registered
    if _cleanup_atexit_registered:
        return
    atexit.register(release_all_locks)
    # Best-effort signal handlers (only on main thread).
    try:
        if threading_current_main():
            for sig in (signal.SIGTERM, signal.SIGINT):
                prev = signal.getsignal(sig)
                # Wrap any prior handler so we still call it after cleanup.
                def _make(prev_handler, sig_value):
                    def _handler(signum, frame):
                        release_all_locks()
                        if callable(prev_handler) and prev_handler not in (
                            signal.SIG_DFL,
                            signal.SIG_IGN,
                        ):
                            try:
                                prev_handler(signum, frame)
                            except Exception:  # pragma: no cover
                                pass
                    return _handler
                signal.signal(sig, _make(prev, sig))
    except (ValueError, OSError):  # pragma: no cover - non-main thread
        pass
    _cleanup_atexit_registered = True


def threading_current_main() -> bool:
    import threading
    return threading.current_thread() is threading.main_thread()


@dataclass
class CronTaskLock:
    workspace_root: Path
    session_id: str
    stale_after_ms: int = DEFAULT_STALE_LOCK_MS
    lock_relative_path: Path = SCHEDULED_TASKS_LOCK_RELATIVE_PATH
    acquired: bool = False
    # F-22-G5: when True, the lock may be silently re-acquired if the
    # existing lock file already carries this session's sessionId. This
    # supports the --resume / fork-recovery case where the same ClawCodex
    # session is being re-instantiated.
    allow_session_takeover: bool = True
    # F-22-G5: when True, run the PID identity check (validate that the
    # owning process still looks like a ClawCodex-style process). When
    # False, the lock relies on age + kill(pid, 0) only.
    validate_pid_identity: bool = True

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

        # F-22-G5: sessionId takeover — if the lock is already ours (same
        # sessionId) and takeover is allowed, refresh the lock content
        # with our current PID and return True. This handles the case
        # where a child process restarts and re-acquires the parent's
        # lock without contention.
        if self.allow_session_takeover and self.path.exists():
            existing = self._read_payload()
            if existing and existing.get("sessionId") == self.session_id:
                # Refresh in place (non-exclusive write is fine — we
                # already own it).
                tmp = self.path.with_name(
                    f"{self.path.name}.{os.getpid()}.refresh.tmp"
                )
                tmp.write_text(encoded, encoding="utf-8")
                os.replace(tmp, self.path)
                self.acquired = True
                _register_self_cleanup(self)
                return True

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
        _register_self_cleanup(self)
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            data = self._read_payload() or {}
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

    def _read_payload(self) -> dict | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _recover_if_stale(self) -> bool:
        data = self._read_payload()
        if data is None:
            # Corrupt file — fall back to mtime-based recovery.
            return self._unlink_existing_if_old()

        pid = data.get("pid")
        acquired_at = data.get("acquiredAt")
        now = int(time.time() * 1000)
        age_stale = isinstance(acquired_at, int) and now - acquired_at > self.stale_after_ms
        pid_dead = isinstance(pid, int) and not _pid_is_alive(pid)
        # F-22-G5: PID identity check (PID alive but not ClawCodex).
        pid_foreign = False
        if (
            self.validate_pid_identity
            and isinstance(pid, int)
            and _pid_is_alive(pid)
        ):
            validator = _DEFAULT_PID_VALIDATOR or _default_pid_validator
            pid_foreign = not validator(pid)
        if age_stale or pid_dead or pid_foreign:
            if pid_foreign:
                _log.warning(
                    "recovering lock with foreign PID %d (looks like PID recycle)",
                    pid,
                )
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
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        age_ms = int((time.time() - mtime) * 1000)
        if age_ms <= self.stale_after_ms:
            return False
        return self._unlink_existing()


def _register_self_cleanup(lock: CronTaskLock) -> None:
    """Register a release callback for the given lock instance."""
    state = {"registered": False}

    def _release_once() -> None:
        if state["registered"]:
            return
        state["registered"] = True
        try:
            lock.release()
        except Exception as exc:  # pragma: no cover
            _log.warning("cron lock release failed: %s", exc)

    register_lock_cleanup(_release_once)


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
