"""WI-3.4 tests — eviction grace + sweeper.

Covers:
* `schedule_eviction` no-ops on non-terminal status / `retain=True`.
* `is_eligible_for_eviction` notify-before-evict guard.
* `sweep_once` drops eligible entries and leaves others.
* Real sweeper thread can be started/stopped without leaking the
  daemon.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace

from src.task_registry import RuntimeTaskRegistry
from src.tasks.eviction import (
    PANEL_GRACE_SECONDS,
    is_eligible_for_eviction,
    schedule_eviction,
    start_eviction_sweeper,
    stop_eviction_sweeper,
    sweep_once,
)
from src.tasks.local_agent import (
    LocalAgentTaskState,
    complete_agent_task,
    register_async_agent,
)
from src.tasks_core import generate_task_id


def _make_terminal_notified(reg: RuntimeTaskRegistry) -> LocalAgentTaskState:
    """Spawn → complete → mark notified. Helper for the eligibility
    tests."""
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="done", registry=reg)
    # Manually mark ``notified=True`` — the production path does this
    # via ``enqueue_agent_notification``, but we test eviction in
    # isolation here.
    reg.update(agent_id, lambda prev: replace(prev, notified=True))
    return reg.get(agent_id)


# ---------------------------------------------------------------------------
# PANEL_GRACE constant — A8 source-confirmed
# ---------------------------------------------------------------------------


def test_panel_grace_seconds_matches_ts_30_seconds() -> None:
    """A8 / TS framework.ts:28 ``PANEL_GRACE_MS = 30_000`` → 30.0s here."""
    assert PANEL_GRACE_SECONDS == 30.0


# ---------------------------------------------------------------------------
# schedule_eviction — pure helper
# ---------------------------------------------------------------------------


def test_schedule_eviction_sets_deadline_for_terminal_state() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_terminal_notified(reg)
    now = 1_000_000.0
    out = schedule_eviction(state, now=now)
    assert out.evict_after == now + PANEL_GRACE_SECONDS


def test_schedule_eviction_noop_for_running_state() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    state = register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    out = schedule_eviction(state)
    assert out.evict_after is None


def test_schedule_eviction_noop_for_retained_terminal() -> None:
    """Retain flag pins the entry — UI is "holding" it."""
    reg = RuntimeTaskRegistry()
    state = _make_terminal_notified(reg)
    retained = replace(state, retain=True)
    out = schedule_eviction(retained)
    assert out.evict_after is None


# ---------------------------------------------------------------------------
# is_eligible_for_eviction — notify-before-evict guard
# ---------------------------------------------------------------------------


def test_eligible_when_notified_and_past_deadline() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_terminal_notified(reg)
    now = state.evict_after + 1.0  # past the deadline
    assert is_eligible_for_eviction(state, now=now) is True


def test_not_eligible_before_deadline() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_terminal_notified(reg)
    now = state.evict_after - 5.0  # before deadline
    assert is_eligible_for_eviction(state, now=now) is False


def test_not_eligible_when_not_notified() -> None:
    """Notify-before-evict guard. A terminal task whose parent hasn't
    been told yet must NOT be evicted — losing the entry would lose
    the ``<task-notification>`` envelope."""
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="done", registry=reg)
    # ``notified`` left at False (deliberately).
    state = reg.get(agent_id)
    now = (state.evict_after or 0) + 100
    assert is_eligible_for_eviction(state, now=now) is False


def test_not_eligible_when_running() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    state = register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    assert is_eligible_for_eviction(state, now=time.time() + 1e9) is False


def test_not_eligible_when_retained() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_terminal_notified(reg)
    retained = replace(state, retain=True)
    assert is_eligible_for_eviction(retained, now=(state.evict_after or 0) + 100) is False


# ---------------------------------------------------------------------------
# sweep_once — drops the eligible entries
# ---------------------------------------------------------------------------


def test_sweep_once_removes_eligible_entries() -> None:
    reg = RuntimeTaskRegistry()
    a = _make_terminal_notified(reg)
    b = _make_terminal_notified(reg)
    # ``c`` is still running — should NOT be swept.
    c_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=c_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    far_future = max(a.evict_after, b.evict_after) + 100

    dropped = sweep_once(reg, now=far_future)

    assert sorted(dropped) == sorted([a.id, b.id])
    assert reg.get(a.id) is None
    assert reg.get(b.id) is None
    assert reg.get(c_id) is not None  # still running


def test_sweep_once_keeps_unnotified_terminal_entries() -> None:
    """Notify-before-evict at the sweep level: entries with
    ``notified=False`` are skipped even when past their deadline."""
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="done", registry=reg)
    # Leave ``notified=False``.
    state = reg.get(agent_id)
    far_future = (state.evict_after or 0) + 100

    dropped = sweep_once(reg, now=far_future)

    assert dropped == []
    assert reg.get(agent_id) is not None


# ---------------------------------------------------------------------------
# start/stop_eviction_sweeper — daemon thread lifecycle
# ---------------------------------------------------------------------------


def test_sweeper_thread_starts_and_stops_cleanly() -> None:
    reg = RuntimeTaskRegistry()
    start_eviction_sweeper(reg, tick_seconds=0.05)
    # Spawn a terminal-notified entry with a past deadline so the
    # sweeper has work to do.
    state = _make_terminal_notified(reg)
    reg.update(state.id, lambda prev: replace(prev, evict_after=time.time() - 1.0))

    # Wait for the sweep to fire.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if reg.get(state.id) is None:
            break
        time.sleep(0.02)

    assert reg.get(state.id) is None, "sweeper did not evict the eligible entry"

    stop_eviction_sweeper(timeout=1.0)
    # No live sweeper thread left in the runtime-task pool.
    live = [t for t in threading.enumerate() if t.name == "runtime-task-eviction-sweeper"]
    assert live == []


def test_sweeper_start_is_idempotent_for_same_registry() -> None:
    reg = RuntimeTaskRegistry()
    start_eviction_sweeper(reg, tick_seconds=0.5)
    start_eviction_sweeper(reg, tick_seconds=0.5)  # second call is a no-op
    live = [t for t in threading.enumerate() if t.name == "runtime-task-eviction-sweeper"]
    assert len(live) == 1
    stop_eviction_sweeper(timeout=1.0)
