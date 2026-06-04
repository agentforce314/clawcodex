"""WI-1.4 acceptance tests — bash background tasks live on runtime_tasks.

Verifies the spawn-writer-and-reaper round-trip lands a typed
``LocalShellTaskState`` on ``context.runtime_tasks`` and keeps the legacy
``context.background_bash_tasks`` view in lockstep for back-compat.
"""
from __future__ import annotations

import time
from pathlib import Path

from src.tasks.local_shell import LocalShellTaskState, is_local_shell_task
from src.tool_system.context import ToolContext
from src.tool_system.tools.bash.background import (
    read_background_output,
    spawn_background_bash,
    stop_background_bash,
)


def _wait_for_status(
    ctx: ToolContext, task_id: str, want: tuple[str, ...], timeout_s: float = 5.0
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = ctx.runtime_tasks.get(task_id)
        if isinstance(state, LocalShellTaskState) and state.status in want:
            return state.status
        time.sleep(0.02)
    state = ctx.runtime_tasks.get(task_id)
    return getattr(state, "status", "<missing>")


def test_spawn_registers_local_shell_task_on_runtime_tasks(tmp_path: Path) -> None:
    """Writer populates ``runtime_tasks`` with a typed LocalShellTaskState."""
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="echo hello",
        cwd=tmp_path,
        description="echo test",
        context=ctx,
    )
    task_id = result["backgroundTaskId"]

    state = ctx.runtime_tasks.get(task_id)
    assert is_local_shell_task(state), f"expected LocalShellTaskState, got {state!r}"
    assert isinstance(state, LocalShellTaskState)  # narrow for the rest
    assert state.id == task_id
    assert state.type == "local_bash"
    assert state.command == "echo hello"
    assert state.description == "echo test"


def test_spawned_task_id_has_b_prefix_and_8_char_body(tmp_path: Path) -> None:
    """WI-1.4 ID format: ``b<8 base36>``, matching chapter Task.ts:79-105."""
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="echo x",
        cwd=tmp_path,
        description=None,
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    assert task_id.startswith("b"), f"missing b prefix: {task_id!r}"
    assert len(task_id) == 9, f"expected 9 chars, got {len(task_id)}: {task_id!r}"


def test_legacy_dict_view_stays_in_lockstep(tmp_path: Path) -> None:
    """Back-compat: ``ctx.background_bash_tasks[id]`` is mirrored alongside
    runtime_tasks during the deprecation cycle. Both must agree on shape
    so unmigrated readers keep working."""
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="echo x",
        cwd=tmp_path,
        description="legacy test",
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    legacy = ctx.background_bash_tasks.get(task_id)
    assert legacy is not None
    assert legacy["task_id"] == task_id
    assert legacy["command"] == "echo x"
    assert legacy["description"] == "legacy test"
    # Required for stop_background_bash to find the Popen handle.
    assert legacy["_proc"] is not None


def test_reaper_updates_runtime_status_to_completed_on_zero_exit(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="true",  # exit 0
        cwd=tmp_path,
        description="reap-completed",
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    final = _wait_for_status(ctx, task_id, ("completed", "failed", "killed"))
    assert final == "completed"
    state = ctx.runtime_tasks.get(task_id)
    assert isinstance(state, LocalShellTaskState)
    assert state.exit_code == 0
    assert state.end_time is not None
    assert state.finished_at is not None


def test_reaper_updates_runtime_status_to_failed_on_nonzero_exit(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="exit 7",
        cwd=tmp_path,
        description="reap-failed",
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    final = _wait_for_status(ctx, task_id, ("completed", "failed", "killed"))
    assert final == "failed"
    state = ctx.runtime_tasks.get(task_id)
    assert isinstance(state, LocalShellTaskState)
    assert state.exit_code == 7


def test_read_background_output_still_works_after_migration(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="echo round_trip",
        cwd=tmp_path,
        description=None,
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    _wait_for_status(ctx, task_id, ("completed", "failed", "killed"))
    snapshot = read_background_output(ctx, task_id)
    assert snapshot is not None
    assert "round_trip" in snapshot["output"]
    assert snapshot["status"] == "completed"


def test_stop_background_bash_returns_false_after_already_exited(tmp_path: Path) -> None:
    """``stop_background_bash`` is the legacy reader path; verify it still
    works against the migrated state shape (the legacy dict still carries
    the Popen handle via the lockstep mirror)."""
    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="true",
        cwd=tmp_path,
        description=None,
        context=ctx,
    )
    task_id = result["backgroundTaskId"]
    _wait_for_status(ctx, task_id, ("completed", "failed", "killed"))
    # Process already exited — stop returns False (nothing to signal).
    assert stop_background_bash(ctx, task_id) is False


def test_taskstop_routes_via_runtime_tasks(tmp_path: Path) -> None:
    """Phase-0 hard-fix-first + Chunk-B branch 1: TaskStop's runtime_tasks
    branch reaches LocalShellTask via per-type kill dispatch and returns a
    valid result."""
    from src.tool_system.tools.task_stop import TaskStopTool

    ctx = ToolContext(workspace_root=tmp_path)
    result = spawn_background_bash(
        command="sleep 30",
        cwd=tmp_path,
        description=None,
        context=ctx,
    )
    task_id = result["backgroundTaskId"]

    import asyncio
    # Post Chunk D / WI-4.0, ``TaskStopTool.call`` is async.
    stop = asyncio.run(TaskStopTool.call({"task_id": task_id}, ctx)).output
    assert stop["task_id"] == task_id
    # ``stopped`` is True iff the Popen has terminated. We don't rely on
    # which side wins the race; we DO rely on the dispatch reaching the
    # bash branch (no error).
    assert "error" not in stop or stop.get("stopped") is True
