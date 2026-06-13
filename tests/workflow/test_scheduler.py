"""Tests for the bounded-concurrency scheduler and per-run agent cap."""

from __future__ import annotations

import asyncio

import pytest

import src.workflow.scheduler as scheduler_mod
from src.workflow.errors import WorkflowLimitError
from src.workflow.scheduler import Scheduler


def test_reserve_assigns_sequential_indices():
    sched = Scheduler(max_concurrent=4)
    assert [sched.reserve() for _ in range(3)] == [0, 1, 2]
    assert sched.launched == 3


def test_default_concurrency_is_gentle(monkeypatch):
    """Default cap is a gentle 4 (rate-limit friendly), not the old min(16, cpu-2).
    A run is network/LLM-bound, so the limiter is the token window, not cores."""
    from src.workflow.constants import DEFAULT_MAX_CONCURRENT_AGENTS, max_concurrent_agents

    monkeypatch.delenv("CLAUDE_CODE_WORKFLOW_MAX_AGENTS", raising=False)
    assert DEFAULT_MAX_CONCURRENT_AGENTS == 4
    assert max_concurrent_agents() == 4
    # a default-constructed Scheduler (what every workflow run gets) uses it
    assert Scheduler().max_concurrent == 4


def test_concurrency_env_override(monkeypatch):
    from src.workflow.constants import max_concurrent_agents

    monkeypatch.setenv("CLAUDE_CODE_WORKFLOW_MAX_AGENTS", "12")
    assert max_concurrent_agents() == 12
    assert Scheduler().max_concurrent == 12
    # garbage falls back to the gentle default
    monkeypatch.setenv("CLAUDE_CODE_WORKFLOW_MAX_AGENTS", "not-a-number")
    assert max_concurrent_agents() == 4


def test_reserve_enforces_per_run_cap(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "MAX_AGENTS_PER_RUN", 2)
    sched = Scheduler(max_concurrent=4)
    sched.reserve()
    sched.reserve()
    with pytest.raises(WorkflowLimitError):
        sched.reserve()


async def test_slot_caps_concurrency():
    sched = Scheduler(max_concurrent=3)
    active = 0
    peak = 0

    async def worker():
        nonlocal active, peak
        async with sched.slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(20)))
    assert peak <= 3
    assert sched.peak_concurrency <= 3


async def test_slot_releases_on_exception():
    sched = Scheduler(max_concurrent=1)

    async def boom():
        async with sched.slot():
            raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await boom()
    # The single slot must be free again afterwards.
    async with sched.slot():
        pass
