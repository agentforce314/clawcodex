"""Session-scoped scheduled-task engine — the executor behind ``/loop``,
``CronCreate``/``CronList``/``CronDelete`` and ``ScheduleWakeup``.

Port of the CC behavior documented in docs/en/scheduled-tasks:

- Tasks are session-scoped and fire BETWEEN turns: the agent-server worker
  polls :meth:`SessionCronScheduler.pop_due` from its idle branch and runs
  each fired prompt as an internal turn (agent_server ``_run_worker``).
- Recurring tasks expire 7 days after creation — the task fires one final
  time past its expiry, then deletes itself. One-shots delete after firing.
- No catch-up for missed fires: however many intervals passed while the
  agent was busy, a due job fires exactly once and then advances.
- A session holds at most 50 jobs; each has an 8-character hex ID.
- Deterministic jitter (§Jitter): recurring jobs fire up to 30 minutes
  after the scheduled time (up to half the interval for jobs that run more
  often than hourly); one-shots pinned to :00 or :30 fire up to 90 seconds
  early. The offset is derived from the job ID, so a given job always gets
  the same offset. Jitter never applies to dynamic wakeups.
- The dynamic-loop wakeup (``ScheduleWakeup``) is a single slot per
  session: delay clamped to [60, 3600] seconds, ``stop`` clears it, and
  pressing Esc between turns clears it (agent-server interrupt control).

Everything is computed in local time (``0 9 * * *`` means 9am local).
Thread-safety: tool calls mutate from the worker thread mid-turn while the
control plane reads snapshots from the asyncio thread, so all access goes
through one lock.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from .cron_expr import CronExpression, describe_cron

__all__ = [
    "CronJob",
    "FiredTask",
    "PendingWakeup",
    "SessionCronScheduler",
    "MAX_JOBS",
    "RECURRING_EXPIRY_SECONDS",
    "WAKEUP_MIN_DELAY_SECONDS",
    "WAKEUP_MAX_DELAY_SECONDS",
    "FALLBACK_WAKEUP_DELAY_SECONDS",
    "scheduled_tasks_disabled",
]

MAX_JOBS = 50
RECURRING_EXPIRY_SECONDS = 7 * 24 * 3600  # §Seven-day expiry
WAKEUP_MIN_DELAY_SECONDS = 60
WAKEUP_MAX_DELAY_SECONDS = 3600
#: Delay for the single fallback wakeup scheduled when a dynamic-loop
#: iteration ends without rescheduling or stopping (§Stop a loop).
FALLBACK_WAKEUP_DELAY_SECONDS = 20 * 60

_TRUTHY = ("1", "true", "yes", "on")


def scheduled_tasks_disabled() -> bool:
    """CLAWCODEX_DISABLE_CRON=1 disables the scheduler entirely (the
    CLAUDE_CODE_DISABLE_CRON spelling is honored for CC parity)."""
    for var in ("CLAWCODEX_DISABLE_CRON", "CLAUDE_CODE_DISABLE_CRON"):
        if os.environ.get(var, "").strip().lower() in _TRUTHY:
            return True
    return False


def _stable_offset(job_id: str, modulo: int) -> int:
    """Deterministic per-job jitter seed in [0, modulo). md5 keeps the
    offset stable across processes (hash() is salted per run)."""
    if modulo <= 0:
        return 0
    digest = hashlib.md5(job_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % modulo


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool
    created_at: float
    next_fire_at: float
    expires_at: Optional[float] = None  # recurring only
    fired_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cron": self.cron,
            "prompt": self.prompt,
            "recurring": self.recurring,
            "durable": self.durable,
            "created_at": self.created_at,
            "next_fire_at": self.next_fire_at,
            "expires_at": self.expires_at,
            "fired_count": self.fired_count,
        }


@dataclass
class PendingWakeup:
    fire_at: float
    prompt: str
    reason: str
    is_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "fire_at": self.fire_at,
            "prompt": self.prompt,
            "reason": self.reason,
            "is_fallback": self.is_fallback,
        }


@dataclass
class FiredTask:
    """One due task popped for execution."""

    kind: str  # "cron" | "wakeup"
    prompt: str
    id: str = ""
    cron: str = ""
    reason: str = ""
    recurring: bool = False
    deleted: bool = False  # one-shot fired / recurring expired
    is_fallback: bool = False


@dataclass
class SessionCronScheduler:
    """All scheduled-prompt state for one agent session."""

    now_fn: Callable[[], float] = time.time
    jitter: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _jobs: dict[str, CronJob] = field(default_factory=dict)
    _wakeup: Optional[PendingWakeup] = None
    #: Last ScheduleWakeup action inside the current turn window — drives
    #: the fallback-wakeup decision after a wakeup-fired iteration.
    _wakeup_action: Optional[str] = None  # "set" | "stopped" | None

    # ── cron jobs ───────────────────────────────────────────────────────

    def _job_jitter_seconds(self, job_id: str, expr: CronExpression, base: datetime) -> float:
        """§Jitter offset for a recurring job's fire at ``base``."""
        nxt = expr.next_after(base)
        interval = (nxt - base).total_seconds()
        cap = int(min(1800.0, interval / 2))
        return float(_stable_offset(job_id, cap + 1))

    def _one_shot_jitter_seconds(self, job_id: str, fire: datetime) -> float:
        """One-shots pinned to :00/:30 fire up to 90 seconds EARLY."""
        if fire.minute in (0, 30):
            return -float(_stable_offset(job_id, 91))
        return 0.0

    def _compute_next_fire(self, job_id: str, expr: CronExpression, recurring: bool,
                           after: float) -> float:
        base = expr.next_after(datetime.fromtimestamp(after))
        fire_at = base.timestamp()
        if not self.jitter:
            return fire_at
        if recurring:
            return fire_at + self._job_jitter_seconds(job_id, expr, base)
        jittered = fire_at + self._one_shot_jitter_seconds(job_id, base)
        # An early offset must not land in the past (it would double-fire
        # the "fires once when idle" rule for a wall-clock-near one-shot).
        return max(jittered, after + 1.0)

    def create(self, cron: str, prompt: str, *, recurring: bool = True,
               durable: bool = False) -> CronJob:
        """Validate + register a job. Raises ValueError on bad input,
        the 50-job cap, or a disabled scheduler."""
        if scheduled_tasks_disabled():
            raise ValueError(
                "scheduled tasks are disabled (CLAWCODEX_DISABLE_CRON is set)"
            )
        expr = CronExpression.parse(cron)  # raises ValueError with detail
        now = self.now_fn()
        job_id = uuid.uuid4().hex[:8]
        with self._lock:
            if len(self._jobs) >= MAX_JOBS:
                raise ValueError(
                    f"scheduled-task limit reached ({MAX_JOBS} per session) — "
                    "delete one with CronDelete first"
                )
            job = CronJob(
                id=job_id,
                cron=cron,
                prompt=prompt,
                recurring=recurring,
                durable=durable,
                created_at=now,
                next_fire_at=self._compute_next_fire(job_id, expr, recurring, now),
                expires_at=(now + RECURRING_EXPIRY_SECONDS) if recurring else None,
            )
            self._jobs[job.id] = job
            return job

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    # ── dynamic-loop wakeup slot ────────────────────────────────────────

    def set_wakeup(self, delay_seconds: float, prompt: str, reason: str,
                   *, is_fallback: bool = False) -> PendingWakeup:
        """Arm (or replace) the single pending wakeup. The delay is clamped
        to [60, 3600] seconds, mirroring the ScheduleWakeup contract."""
        if scheduled_tasks_disabled():
            raise ValueError(
                "scheduled tasks are disabled (CLAWCODEX_DISABLE_CRON is set)"
            )
        clamped = min(max(float(delay_seconds), float(WAKEUP_MIN_DELAY_SECONDS)),
                      float(WAKEUP_MAX_DELAY_SECONDS))
        wakeup = PendingWakeup(
            fire_at=self.now_fn() + clamped,
            prompt=prompt,
            reason=reason,
            is_fallback=is_fallback,
        )
        with self._lock:
            self._wakeup = wakeup
            if not is_fallback:
                self._wakeup_action = "set"
            return wakeup

    def clear_wakeup(self) -> bool:
        """Drop the pending wakeup (ScheduleWakeup stop:true, or Esc while
        idle). Returns whether one was pending."""
        with self._lock:
            had = self._wakeup is not None
            self._wakeup = None
            self._wakeup_action = "stopped"
            return had

    def wakeup_info(self) -> Optional[PendingWakeup]:
        with self._lock:
            return self._wakeup

    def begin_turn_window(self) -> None:
        """Reset the ScheduleWakeup action tracker before running a
        wakeup-fired turn; :meth:`wakeup_action_since` reads what the turn
        did (rescheduled / stopped / nothing) for the fallback decision."""
        with self._lock:
            self._wakeup_action = None

    def wakeup_action_since(self) -> Optional[str]:
        with self._lock:
            return self._wakeup_action

    # ── firing ──────────────────────────────────────────────────────────

    def pop_due(self, now: Optional[float] = None) -> list[FiredTask]:
        """Pop every task whose fire time has passed. Called from the
        worker's idle branch, so a fire can never interleave with a turn."""
        if scheduled_tasks_disabled():
            return []
        now_t = self.now_fn() if now is None else now
        fired: list[FiredTask] = []
        with self._lock:
            if self._wakeup is not None and self._wakeup.fire_at <= now_t:
                wakeup, self._wakeup = self._wakeup, None
                fired.append(FiredTask(
                    kind="wakeup",
                    prompt=wakeup.prompt,
                    reason=wakeup.reason,
                    is_fallback=wakeup.is_fallback,
                ))
            for job in sorted(self._jobs.values(), key=lambda j: j.next_fire_at):
                if job.next_fire_at > now_t:
                    continue
                job.fired_count += 1
                expired = (
                    job.recurring
                    and job.expires_at is not None
                    and now_t >= job.expires_at
                )
                if not job.recurring or expired:
                    # One-shot: delete after firing. Recurring past expiry:
                    # fires one final time, then deletes itself.
                    del self._jobs[job.id]
                    deleted = True
                else:
                    # No catch-up: one fire, then advance from NOW.
                    expr = CronExpression.parse(job.cron)
                    job.next_fire_at = self._compute_next_fire(
                        job.id, expr, True, now_t
                    )
                    deleted = False
                fired.append(FiredTask(
                    kind="cron",
                    prompt=job.prompt,
                    id=job.id,
                    cron=job.cron,
                    recurring=job.recurring,
                    deleted=deleted,
                ))
        return fired

    # ── snapshot / restore (TUI events + session persistence) ──────────

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "jobs": [job.to_dict() for job in
                         sorted(self._jobs.values(), key=lambda j: j.created_at)],
                "wakeup": self._wakeup.to_dict() if self._wakeup else None,
            }

    def restore(self, snapshot: dict[str, Any]) -> int:
        """Re-arm persisted tasks on session resume, applying the resume
        rules: recurring jobs still inside their 7-day window come back,
        one-shots whose time already passed are dropped, and a pending
        wakeup survives only if its fire time is still in the future.
        Returns how many tasks (jobs + wakeup) were restored."""
        if not isinstance(snapshot, dict):
            return 0
        now = self.now_fn()
        restored = 0
        with self._lock:
            for raw in snapshot.get("jobs") or []:
                try:
                    job = CronJob(
                        id=str(raw["id"]),
                        cron=str(raw["cron"]),
                        prompt=str(raw["prompt"]),
                        recurring=bool(raw.get("recurring", True)),
                        durable=bool(raw.get("durable", False)),
                        created_at=float(raw.get("created_at") or now),
                        next_fire_at=float(raw.get("next_fire_at") or 0.0),
                        expires_at=(
                            float(raw["expires_at"])
                            if raw.get("expires_at") is not None else None
                        ),
                        fired_count=int(raw.get("fired_count") or 0),
                    )
                    CronExpression.parse(job.cron)
                except (KeyError, TypeError, ValueError):
                    continue
                if job.recurring:
                    if job.expires_at is not None and now >= job.expires_at:
                        continue  # expired while away
                else:
                    if job.next_fire_at <= now:
                        continue  # one-shot whose time has passed
                if len(self._jobs) >= MAX_JOBS:
                    break
                self._jobs[job.id] = job
                restored += 1
            raw_wakeup = snapshot.get("wakeup")
            if isinstance(raw_wakeup, dict):
                try:
                    fire_at = float(raw_wakeup["fire_at"])
                    if fire_at > now:
                        self._wakeup = PendingWakeup(
                            fire_at=fire_at,
                            prompt=str(raw_wakeup.get("prompt") or ""),
                            reason=str(raw_wakeup.get("reason") or ""),
                            is_fallback=bool(raw_wakeup.get("is_fallback", False)),
                        )
                        restored += 1
                except (KeyError, TypeError, ValueError):
                    pass
        return restored

    # ── display helpers ────────────────────────────────────────────────

    @staticmethod
    def human_schedule(cron: str) -> str:
        try:
            return describe_cron(cron)
        except ValueError:
            return cron
