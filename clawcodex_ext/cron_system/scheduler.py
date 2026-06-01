"""Cron scheduler lifecycle independent of frontends (F-22-G1/G5/G8/G7)."""

from __future__ import annotations

import atexit
import logging
import signal
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .lock import CronTaskLock
from .models import CronJitterConfig, CronTask, load_jitter_config
from .notifications import build_missed_task_notification
from .runs import CronRun, create_queued_run_for_task
from .tasks import (
    find_due_tasks,
    find_missed_tasks,
    mark_cron_tasks_fired,
    now_ms,
    prune_expired_recurring_tasks,
    read_cron_tasks,
    remove_missed_tasks,
)

_log = logging.getLogger(__name__)

# Optional event hook signatures for F-22-G7 (analytics reservation).
# Defaults are no-ops; callers (REPL, daemon) can pass observability sinks.
FireEventHook = Callable[[dict], None]
MissedEventHook = Callable[[dict], None]
ExpiredEventHook = Callable[[dict], None]


def _noop_event(_payload: dict) -> None:  # pragma: no cover - default hook
    return None


@dataclass
class CronScheduler:
    workspace_root: Path
    on_fire: Callable[[str], None]
    on_fire_task: Callable[[CronTask, CronRun], None] | None = None
    on_missed: Callable[[list[CronTask], str], None] | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    check_interval_seconds: float = 1.0
    # F-22-G1: polled before each tick; when True, scheduler stops firing.
    is_killed: Callable[[], bool] | None = None
    # F-22-G2: per-tick config loader. When None, falls back to
    # ``load_jitter_config(workspace_root)`` so live edits to
    # ``.claude/cron_jitter_config.json`` and ``CLAWCODEX_CRON_*`` env
    # vars take effect on the next ``check_once`` without restarting the
    # CLI. Pass an explicit callable to inject a GrowthBook-style
    # remote-config source.
    load_jitter_config: Callable[[], CronJitterConfig] | None = None
    # F-22-G7: optional event hooks for analytics. No-op by default.
    on_fire_event: FireEventHook = _noop_event
    on_missed_event: MissedEventHook = _noop_event
    on_expired_event: ExpiredEventHook = _noop_event

    _thread: threading.Thread | None = field(default=None, init=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: CronTaskLock | None = field(default=None, init=False)
    # F-22-G2: cache the most recent jitter config so downstream callers
    # (e.g. ``prune_expired_recurring_tasks``) can pick up the live
    # ``recurring_max_age_ms`` without re-reading the loader twice.
    _last_jitter_config: CronJitterConfig | None = field(default=None, init=False)
    # F-22-G8: thread-safe in-flight set to prevent double-fire on async
    # mark_fired / remove windows.
    _in_flight: set[str] = field(default_factory=set, init=False)
    _in_flight_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    # F-22-G5: track whether we registered atexit/signal cleanup so stop()
    # is idempotent.
    _atexit_registered: bool = field(default=False, init=False)
    _signal_registered: bool = field(default=False, init=False)
    _previous_sigterm: signal._HANDLER | None = field(default=None, init=False)
    _previous_sigint: signal._HANDLER | None = field(default=None, init=False)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        lock = CronTaskLock(self.workspace_root, self.session_id)
        if not lock.acquire():
            return False
        self._lock = lock
        self._stop_event.clear()
        self._register_cleanup_hooks()
        self._thread = threading.Thread(
            target=self._run_loop, name="clawcodex-cron-scheduler", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._lock:
            self._lock.release()
            self._lock = None
        self._unregister_cleanup_hooks()

    def load(self) -> list[CronTask]:
        return read_cron_tasks(self.workspace_root)

    def is_disabled(self) -> bool:
        """F-22-G1: True if the kill switch is engaged this tick."""
        if self.is_killed is None:
            return False
        try:
            return bool(self.is_killed())
        except Exception:  # pragma: no cover - defensive
            return False

    def check_once(self, at_ms: int | None = None) -> list[CronTask]:
        if self.is_disabled():
            return []
        # F-22-G2: refresh the live jitter config on every tick so ops
        # can widen the window mid-session without restarting the CLI.
        try:
            self._last_jitter_config = self._resolve_jitter_config()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("jitter config reload failed: %s; using cached", exc)
            if self._last_jitter_config is None:
                self._last_jitter_config = load_jitter_config(self.workspace_root)
        timestamp = at_ms if at_ms is not None else now_ms()
        prune_expired_recurring_tasks(
            self.workspace_root,
            timestamp,
            max_age_ms=self._last_jitter_config.recurring_max_age_ms,
        )
        due = find_due_tasks(self.workspace_root, timestamp)
        if not due:
            return []
        fired: list[CronTask] = []
        for task in due:
            if self._in_flight_contains(task.id):
                continue
            self._in_flight_add(task.id)
            try:
                run = create_queued_run_for_task(self.workspace_root, task, queued_at=timestamp)
                if run is None:
                    continue
                fired.append(task)
                # F-22-G7: fire event.
                self.on_fire_event(
                    {
                        "type": "fire",
                        "task_id": task.id,
                        "recurring": task.recurring,
                        "fire_at": timestamp,
                    }
                )
                if self.on_fire_task is not None:
                    self.on_fire_task(task, run)
                else:
                    self.on_fire(task.prompt)
            finally:
                self._in_flight_remove(task.id)
        mark_cron_tasks_fired(self.workspace_root, fired, timestamp)
        return fired

    def _resolve_jitter_config(self) -> CronJitterConfig:
        if self.load_jitter_config is not None:
            return self.load_jitter_config()
        return load_jitter_config(self.workspace_root)

    def get_jitter_config(self) -> CronJitterConfig:
        """Return the most recently loaded jitter config (refreshes if needed)."""
        if self._last_jitter_config is None:
            self._last_jitter_config = self._resolve_jitter_config()
        return self._last_jitter_config

    def notify_missed_once(self, at_ms: int | None = None) -> list[CronTask]:
        if self.is_disabled():
            return []
        missed = find_missed_tasks(self.workspace_root, at_ms)
        if missed:
            remove_missed_tasks(self.workspace_root, missed)
            # F-22-G7: missed event.
            self.on_missed_event(
                {
                    "type": "missed",
                    "count": len(missed),
                    "task_ids": [t.id for t in missed],
                }
            )
            if self.on_missed is not None:
                self.on_missed(missed, build_missed_task_notification(missed))
        return missed

    def get_next_fire_time(self) -> int | None:
        if self.is_disabled():
            return None
        values = [
            task.next_fire_at
            for task in read_cron_tasks(self.workspace_root)
            if task.next_fire_at is not None
        ]
        return min(values) if values else None

    # ---- F-22-G8 in-flight helpers ----
    def _in_flight_contains(self, task_id: str) -> bool:
        with self._in_flight_lock:
            return task_id in self._in_flight

    def _in_flight_add(self, task_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight.add(task_id)

    def _in_flight_remove(self, task_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight.discard(task_id)

    # ---- F-22-G5 cleanup registry ----
    def _register_cleanup_hooks(self) -> None:
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        if not self._signal_registered and threading.current_thread() is threading.main_thread():
            try:
                self._previous_sigterm = signal.signal(signal.SIGTERM, self._signal_cleanup)
                self._previous_sigint = signal.signal(signal.SIGINT, self._signal_cleanup)
                self._signal_registered = True
            except (ValueError, OSError):  # not main thread / unsupported
                pass

    def _unregister_cleanup_hooks(self) -> None:
        if self._atexit_registered:
            try:
                atexit.unregister(self.stop)
            except Exception:  # pragma: no cover
                pass
            self._atexit_registered = False
        if self._signal_registered:
            try:
                signal.signal(signal.SIGTERM, self._previous_sigterm or signal.SIG_DFL)
                signal.signal(signal.SIGINT, self._previous_sigint or signal.SIG_DFL)
            except (ValueError, OSError):  # pragma: no cover
                pass
            self._signal_registered = False

    def _signal_cleanup(self, signum, frame):  # pragma: no cover - signal path
        self.stop()
        prev = (
            self._previous_sigterm if signum == signal.SIGTERM else self._previous_sigint
        )
        if prev and prev not in (signal.SIG_DFL, signal.SIG_IGN, None):
            try:
                prev(signum, frame)
            except Exception:  # pragma: no cover
                pass

    def _run_loop(self) -> None:
        self.notify_missed_once()
        while not self._stop_event.is_set():
            if self.is_disabled():
                self._stop_event.wait(self.check_interval_seconds)
                continue
            self.check_once()
            self._stop_event.wait(self.check_interval_seconds)
