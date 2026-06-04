"""WI-5.1 + WI-5.2 tests — typed ``stop_task()`` dispatch + error codes.

Covers the ``stop_task()`` helper directly (separate from the tool
layer's argument plumbing). Each TS-canonical error code has a
dedicated test, plus the Python-specific ``kill_timeout``. Also
covers the legacy ``task_manager`` fallback branch and the
race-vs-natural-completion scenario.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    LocalAgentTaskState,
    complete_agent_task,
    register_async_agent,
)
from src.tasks.local_shell import LocalShellTask, LocalShellTaskState
from src.tasks.stop_task import (
    StopTaskError,
    StopTaskResult,
    stop_task,
)
from src.tasks_core import generate_task_id
from src.tool_system.context import ToolContext


# ---------------------------------------------------------------------------
# Result + Error dataclass shape
# ---------------------------------------------------------------------------


def test_stop_task_result_is_error_property_reflects_error_field() -> None:
    ok = StopTaskResult(stopped=True, task_id="b1", task_type="local_bash")
    assert ok.is_error is False

    err = StopTaskResult(
        stopped=False,
        task_id="b1",
        error=StopTaskError(code="not_found", message="x"),
    )
    assert err.is_error is True


def test_stop_task_result_is_frozen() -> None:
    """Frozen dataclass — callers can't accidentally mutate the result."""
    r = StopTaskResult(stopped=True, task_id="b1")
    with pytest.raises(Exception):
        r.stopped = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Error code 1 — not_found (no such task in any registry)
# ---------------------------------------------------------------------------


def test_not_found_when_id_missing_from_all_registries(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    result = asyncio.run(stop_task("nope-doesnt-exist", ctx))
    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "not_found"
    assert "No task found" in result.error.message
    assert result.stopped is False


# ---------------------------------------------------------------------------
# Error code 2 — not_running (task exists but already terminal)
# ---------------------------------------------------------------------------


def test_not_running_when_task_already_completed(tmp_path: Path) -> None:
    """Per chapter / TS: a kill against an already-terminal task surfaces
    as ``is_error=True`` with ``not_running`` — model-friendly because it
    tells the agent the task already finished, not that the kill broke."""
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    complete_agent_task(agent_id, result_text="done", registry=ctx.runtime_tasks)

    result = asyncio.run(stop_task(agent_id, ctx))

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "not_running"
    assert "completed" in result.error.message.lower()


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "killed"])
def test_not_running_for_each_terminal_status(
    tmp_path: Path, terminal_status: str
) -> None:
    """All three chapter terminal statuses produce ``not_running``."""
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    # Force the terminal status in-place; we're not testing the
    # transition, just the dispatch behavior on a terminal entry.
    ctx.runtime_tasks.update(
        agent_id, lambda prev: replace(prev, status=terminal_status)  # type: ignore[arg-type]
    )

    result = asyncio.run(stop_task(agent_id, ctx))
    assert result.error is not None
    assert result.error.code == "not_running"


# ---------------------------------------------------------------------------
# Error code 3 — unsupported_type (no kill impl registered)
# ---------------------------------------------------------------------------


def test_unsupported_type_when_no_kill_impl(tmp_path: Path) -> None:
    """A task with an unregistered ``TaskType`` surfaces as
    ``unsupported_type``. Per critic Chunk-E nit N2: rather than
    relying on a specific TaskType (e.g. ``dream``) staying
    unregistered forever, mock ``get_task_by_type`` to return None.
    Decouples the test from registry contents — a future registration
    of ``dream`` won't silently break this assertion."""
    from src.tasks_core import TaskStateBase

    ctx = ToolContext(workspace_root=tmp_path)
    state = TaskStateBase(
        id="t1cvvvvvz",
        type="local_bash",  # any type; the mock makes lookup return None
        status="running",
        description="x",
        start_time=time.time(),
        output_file="/tmp/x",
    )
    ctx.runtime_tasks.upsert(state)

    with patch("src.tasks.stop_task.get_task_by_type", return_value=None):
        result = asyncio.run(stop_task("t1cvvvvvz", ctx))

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "unsupported_type"
    assert "local_bash" in result.error.message
    assert result.task_type == "local_bash"


# ---------------------------------------------------------------------------
# Error code 4 — kill_timeout (Python-specific extension)
# ---------------------------------------------------------------------------


def test_kill_timeout_when_kill_coroutine_exceeds_budget(tmp_path: Path) -> None:
    """M1 regression — preserved through the WI-5.1 hoist. A hung
    ``Task.kill`` adapter must surface as ``kill_timeout`` rather than
    silently claiming success on a still-running task."""
    ctx = ToolContext(workspace_root=tmp_path)
    task_id = generate_task_id("local_bash")
    state = LocalShellTaskState(
        id=task_id,
        type="local_bash",
        status="running",
        description="hang",
        start_time=time.time(),
        output_file="/tmp/x",
        command="sleep 30",
        cwd="/tmp",
    )
    ctx.runtime_tasks.upsert(state)

    async def _hang(_self, _task_id, _registry) -> None:
        await asyncio.sleep(20.0)

    with patch.object(LocalShellTask, "kill", new=_hang):
        start = time.time()
        result = asyncio.run(stop_task(task_id, ctx))
        elapsed = time.time() - start

    assert elapsed < 7.0, f"kill should bound at 5s, took {elapsed:.1f}s"
    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "kill_timeout"
    assert "5s" in result.error.message
    assert result.stopped is False


# ---------------------------------------------------------------------------
# Happy path — typed dispatch via Task.kill
# ---------------------------------------------------------------------------


def test_typed_dispatch_via_get_task_by_type(tmp_path: Path) -> None:
    """Sanity — a registered ``LocalShellTaskState`` with no live Popen
    still dispatches cleanly. ``stopped`` is False because there's no
    process to signal, but no error code is set."""
    ctx = ToolContext(workspace_root=tmp_path)
    task_id = generate_task_id("local_bash")
    state = LocalShellTaskState(
        id=task_id,
        type="local_bash",
        status="running",
        description="x",
        start_time=time.time(),
        output_file="/tmp/x",
        command="echo x",
        cwd="/tmp",
    )
    ctx.runtime_tasks.upsert(state)

    result = asyncio.run(stop_task(task_id, ctx))

    assert result.is_error is False
    assert result.task_type == "local_bash"
    assert result.task_id == task_id


def test_kill_a_running_local_agent_flips_status(tmp_path: Path) -> None:
    """Stopping a ``local_agent`` task drives the lifecycle helper —
    status → killed, abort_event signalled, eviction scheduled."""
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    # Inject a fresh asyncio.Event so we can verify the signal propagates.
    event = asyncio.Event()
    ctx.runtime_tasks.update(
        agent_id, lambda prev: replace(prev, abort_event=event)
    )

    result = asyncio.run(stop_task(agent_id, ctx))

    assert result.is_error is False
    assert result.task_type == "local_agent"
    state = ctx.runtime_tasks.get(agent_id)
    assert isinstance(state, LocalAgentTaskState)
    assert state.status == "killed"
    assert event.is_set()


# ---------------------------------------------------------------------------
# Legacy task_manager fallback — option (b) per Chunk E brief
# ---------------------------------------------------------------------------


def test_task_manager_fallback_dispatches_via_managed_task(tmp_path: Path) -> None:
    """Legacy ``ManagedTask`` entries still resolve via ``stop_task()``
    after the WI-5.1 hoist. Per Chunk E option (b): the fallback
    branch is documented for removal once ManagedTask is fully
    migrated to the typed registry."""
    ctx = ToolContext(workspace_root=tmp_path)

    def target(stop_event):
        while not stop_event.is_set():
            time.sleep(0.01)

    managed = ctx.task_manager.start(name="loop", target=target)

    result = asyncio.run(stop_task(managed.task_id, ctx))

    assert result.is_error is False
    assert result.stopped is True
    assert result.task_id == managed.task_id
    assert result.task_type == "managed_thread"
    assert managed.stop_event.is_set()


# ---------------------------------------------------------------------------
# Race scenario — natural completion lands while stop is in flight
# ---------------------------------------------------------------------------


def test_race_natural_completion_wins_returns_not_running(tmp_path: Path) -> None:
    """If a task completes naturally before ``stop_task()`` reaches the
    typed-dispatch branch, the registry already shows ``completed``;
    ``stop_task`` returns ``not_running`` (the chapter-correct
    behavior — the task did its job; trying to stop a finished task
    is ``not_running``, not an error from kill)."""
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    # Natural completion lands first.
    complete_agent_task(agent_id, result_text="natural", registry=ctx.runtime_tasks)

    # Now stop_task races into the picture. It sees terminal status
    # and returns ``not_running``.
    result = asyncio.run(stop_task(agent_id, ctx))
    assert result.error is not None
    assert result.error.code == "not_running"
    assert "completed" in result.error.message.lower()


def test_race_literal_concurrent_completion_and_stop(tmp_path: Path) -> None:
    """Critic Chunk-E nit N1: belt-and-suspenders — the prior test is
    deterministic-post-race (pre-complete, then stop). This version
    forces a literal race via ``threading.Barrier(2)``: completion
    and stop_task fire in parallel. The atomic registry mutator means
    only one wins; whichever loses sees a terminal entry and reports
    ``not_running`` (or the kill ran first and stop_task succeeded).
    Either outcome is correct; the failure mode this guards against
    is "both flips clobber each other and the state is inconsistent."
    """
    import threading

    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )

    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def _stop_thread() -> None:
        barrier.wait()
        results["stop"] = asyncio.run(stop_task(agent_id, ctx))

    def _complete_thread() -> None:
        barrier.wait()
        complete_agent_task(agent_id, result_text="natural", registry=ctx.runtime_tasks)
        results["complete"] = True

    t1 = threading.Thread(target=_stop_thread)
    t2 = threading.Thread(target=_complete_thread)
    t1.start(); t2.start()
    t1.join(); t2.join()

    final = ctx.runtime_tasks.get(agent_id)
    # Only one of the terminal flips wins (atomic registry mutator).
    assert final.status in ("completed", "killed")
    # The stop_task result is consistent — either it killed first
    # (stopped=True, no error) or completion won (stopped=False,
    # not_running error).
    stop_result = results["stop"]
    if stop_result.is_error:
        assert stop_result.error is not None
        assert stop_result.error.code == "not_running"
    else:
        # Stop won; final status should be killed.
        assert final.status == "killed"


# ---------------------------------------------------------------------------
# task_stop tool layer — formats StopTaskResult into ToolResult
# ---------------------------------------------------------------------------


def test_tool_layer_promotes_error_code_to_top_level(tmp_path: Path) -> None:
    """``_task_stop_call`` (Chunk E thin shim) flattens the
    ``StopTaskError`` into ``output.error_code`` and ``output.error``
    so existing tests / model contracts keep matching."""
    from src.tool_system.tools.task_stop import TaskStopTool

    ctx = ToolContext(workspace_root=tmp_path)
    result = asyncio.run(
        TaskStopTool.call({"task_id": "missing"}, ctx)
    )

    assert result.is_error is True
    assert result.output["error_code"] == "not_found"
    assert "No task found" in result.output["error"]
    assert result.output["task_id"] == "missing"
