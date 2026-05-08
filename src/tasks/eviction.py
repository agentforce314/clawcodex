"""Eviction sweeper for terminal-state runtime tasks — Chunk D / WI-3.4.

Mirrors ``PANEL_GRACE_MS`` + the eviction-after-deadline behavior
described in chapter §"TaskStop" final paragraph + plan WI-3.4.

Per assumption A8 (TS source-confirmed at
``typescript/src/utils/task/framework.ts:28``): the grace period is
30 seconds. The sweeper runs every 5 seconds, so the worst-case
eviction lag is ≤35 seconds.

Constraints:
1. **Notify before evict.** A terminal task without ``notified=True``
   must NOT be evicted — the parent agent hasn't been told the task
   finished yet, and dropping the entry would lose the
   ``<task-notification>`` envelope. The sweeper skips entries with
   ``notified=False``.
2. **Daemon thread, cancel-on-shutdown.** The sweeper runs on a daemon
   thread so it never blocks process exit. ``stop_eviction_sweeper``
   is exposed for clean shutdown in tests and explicit teardown paths.
3. **Idempotent start.** ``start_eviction_sweeper`` is safe to call
   multiple times; only the first call spawns the thread.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.tasks_core import TaskStateBase, is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry

logger = logging.getLogger(__name__)


# Per assumption A8 / TS framework.ts:28. Do not adjust without
# revisiting the chapter §"TaskStop" rationale.
PANEL_GRACE_SECONDS: float = 30.0

# Sweeper tick — 5s tick over 30s grace bounds eviction lag at ≤35s
# (worst case: terminal transition lands one tick after a sweep, sits
# the full grace, waits up to one more tick for the next sweep).
_SWEEPER_TICK_SECONDS: float = 5.0


def schedule_eviction(
    state: TaskStateBase,
    *,
    grace_seconds: float = PANEL_GRACE_SECONDS,
    now: float | None = None,
) -> TaskStateBase:
    """Return a copy of ``state`` with ``evict_after`` set to ``now + grace``.

    Pure helper — does not touch the registry. Used inside lifecycle
    mutators (``complete_agent_task`` etc.) to schedule the eviction
    deadline atomically with the terminal-state transition.

    Behavior matches TS LocalAgentTask.tsx:294
    (``evictAfter: task.retain ? undefined : Date.now() + PANEL_GRACE_MS``):

    * **Terminal + not retained** → set ``evict_after = now + grace``.
    * **Terminal + retained** → CLEAR ``evict_after = None`` (retain
      pins the entry; existing deadline must be removed so a future
      ``retain`` flip doesn't leave a stale deadline pointing at a
      moment in the past).
    * **Non-terminal** → leave the state unchanged. ``evict_after`` only
      makes sense for terminal entries.
    * **State without ``evict_after`` field** → identity (defensive).
    """
    if not hasattr(state, "evict_after"):
        return state
    if not is_terminal_task_status(state.status):
        return state
    if getattr(state, "retain", False):
        # Retain pins the entry; clear any stale deadline.
        if state.evict_after is None:  # type: ignore[attr-defined]
            return state  # already clear
        return replace(state, evict_after=None)  # type: ignore[type-var]
    deadline = (now if now is not None else time.time()) + grace_seconds
    return replace(state, evict_after=deadline)  # type: ignore[type-var]


def is_eligible_for_eviction(state: TaskStateBase, *, now: float | None = None) -> bool:
    """Predicate for the sweeper.

    Eligible iff:
    * status is terminal, AND
    * notified=True (don't drop entries before the parent has been told), AND
    * evict_after is set and in the past, AND
    * retain=False (UI hasn't pinned the entry).
    """
    if not is_terminal_task_status(state.status):
        return False
    if not state.notified:
        # Notify-before-evict guard. The parent agent's run loop will
        # surface the notification; only after that does eviction make
        # sense.
        return False
    deadline = getattr(state, "evict_after", None)
    if deadline is None:
        return False
    if getattr(state, "retain", False):
        return False
    moment = now if now is not None else time.time()
    return moment >= deadline


def sweep_once(
    registry: "RuntimeTaskRegistry",
    *,
    now: float | None = None,
) -> list[str]:
    """Walk every entry, evict the eligible ones, return the dropped IDs.

    Snapshot-then-remove pattern (the registry's ``all()`` returns a
    snapshot list; the ``remove`` calls land outside the snapshot
    iteration). No mutator runs under the lock during eviction —
    ``remove`` is a single dict-pop guarded by the registry's RLock.
    """
    moment = now if now is not None else time.time()
    dropped: list[str] = []
    for state in registry.all():
        if is_eligible_for_eviction(state, now=moment):
            if registry.remove(state.id):
                dropped.append(state.id)
                logger.debug(
                    "evicted terminal task %s (status=%s, notified=True)",
                    state.id, state.status,
                )
    return dropped


# ---------------------------------------------------------------------------
# Background sweeper thread — daemon, cancel-on-shutdown
# ---------------------------------------------------------------------------


_sweeper_lock = threading.Lock()
_sweeper_thread: threading.Thread | None = None
_sweeper_stop = threading.Event()
_sweeper_registry: "RuntimeTaskRegistry | None" = None


def start_eviction_sweeper(
    registry: "RuntimeTaskRegistry",
    *,
    tick_seconds: float = _SWEEPER_TICK_SECONDS,
) -> None:
    """Start the background eviction sweeper for ``registry``.

    Idempotent — re-calling with the same registry is a no-op; the
    existing thread keeps running. Calling with a *different* registry
    is treated as a re-bind (stop the old thread, start a new one);
    test fixtures construct fresh registries per test.
    """
    global _sweeper_thread, _sweeper_registry
    with _sweeper_lock:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            if _sweeper_registry is registry:
                return
            # Different registry — tear down and re-arm.
            _sweeper_stop.set()
            _sweeper_thread.join(timeout=2.0)
            _sweeper_thread = None
        _sweeper_stop = threading.Event()  # noqa: F841 (rebound below)
        # Replace the module-level event with a fresh one for the new run.
        globals()["_sweeper_stop"] = _sweeper_stop
        _sweeper_registry = registry
        thread = threading.Thread(
            target=_sweeper_loop,
            args=(registry, tick_seconds, _sweeper_stop),
            name="runtime-task-eviction-sweeper",
            daemon=True,
        )
        _sweeper_thread = thread
        thread.start()


def stop_eviction_sweeper(*, timeout: float = 2.0) -> None:
    """Signal the sweeper to exit and wait for it. Safe to call when no
    sweeper is running."""
    global _sweeper_thread, _sweeper_registry
    with _sweeper_lock:
        if _sweeper_thread is None:
            return
        thread = _sweeper_thread
        _sweeper_stop.set()
        _sweeper_thread = None
        _sweeper_registry = None
    thread.join(timeout=timeout)


def _sweeper_loop(
    registry: "RuntimeTaskRegistry",
    tick_seconds: float,
    stop_event: threading.Event,
) -> None:
    """Background thread body — sleep tick, sweep, repeat until stopped."""
    while not stop_event.is_set():
        # Use the stop event as the sleep so we cancel promptly on
        # shutdown rather than waiting out a full tick.
        if stop_event.wait(timeout=tick_seconds):
            return
        try:
            sweep_once(registry)
        except Exception:
            # The sweeper must never die from a transient error;
            # logging + continuing is the right policy.
            logger.exception("eviction sweeper iteration failed")


__all__ = [
    "PANEL_GRACE_SECONDS",
    "schedule_eviction",
    "is_eligible_for_eviction",
    "sweep_once",
    "start_eviction_sweeper",
    "stop_eviction_sweeper",
]
