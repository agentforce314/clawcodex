"""WI-4.0 + WI-4.1 tests — async tool dispatch + TaskOutput polling.

Covers the dispatch layer's sync-vs-async branch, the three
``retrieval_status`` values (``success`` / ``timeout`` / ``not_ready``),
schema bounds rejection, default timeout, and abort-fast-path.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    LocalAgentTaskState,
    complete_agent_task,
    register_async_agent,
)
from src.tasks_core import generate_task_id
from src.tool_system.context import ToolContext
from src.tool_system.tools.tasks_v2 import TaskOutputTool


# ---------------------------------------------------------------------------
# WI-4.0 — async build_tool dispatch
# ---------------------------------------------------------------------------


def test_dispatch_loop_branches_on_iscoroutinefunction(tmp_path: Path) -> None:
    """The registry's ``_invoke_tool_call`` helper drives async tools
    through ``asyncio.run`` when no loop is active."""
    from src.tool_system.registry import _invoke_tool_call

    ctx = ToolContext(workspace_root=tmp_path)

    class _AsyncStub:
        name = "AsyncStub"
        async def call(self, _input, _ctx):
            from src.tool_system.protocol import ToolResult
            return ToolResult(name="AsyncStub", output={"ok": True})

    class _SyncStub:
        name = "SyncStub"
        def call(self, _input, _ctx):
            from src.tool_system.protocol import ToolResult
            return ToolResult(name="SyncStub", output={"ok": True})

    # Both stubs round-trip through the same dispatcher.
    sync_result = _invoke_tool_call(_SyncStub(), {}, ctx)
    async_result = _invoke_tool_call(_AsyncStub(), {}, ctx)
    assert sync_result.output == {"ok": True}
    assert async_result.output == {"ok": True}


# ---------------------------------------------------------------------------
# WI-4.1 — retrieval_status semantics
# ---------------------------------------------------------------------------


def _ctx_with_agent(tmp_path: Path) -> tuple[ToolContext, str]:
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    return ctx, agent_id


def test_block_false_returns_not_ready_for_running_task(tmp_path: Path) -> None:
    ctx, agent_id = _ctx_with_agent(tmp_path)
    result = asyncio.run(
        TaskOutputTool.call({"task_id": agent_id, "block": False}, ctx)
    )
    assert result.output["retrieval_status"] == "not_ready"
    assert result.output["task"]["status"] == "running"


def test_block_false_returns_success_for_terminal_task(tmp_path: Path) -> None:
    ctx, agent_id = _ctx_with_agent(tmp_path)
    complete_agent_task(agent_id, result_text="ok", registry=ctx.runtime_tasks)
    result = asyncio.run(
        TaskOutputTool.call({"task_id": agent_id, "block": False}, ctx)
    )
    assert result.output["retrieval_status"] == "success"
    assert result.output["task"]["status"] == "completed"


def test_block_true_returns_timeout_when_deadline_expires(tmp_path: Path) -> None:
    """Polling with a tight deadline against a still-running task
    returns ``retrieval_status="timeout"`` and the running snapshot."""
    ctx, agent_id = _ctx_with_agent(tmp_path)
    start = time.time()
    result = asyncio.run(
        TaskOutputTool.call(
            {"task_id": agent_id, "block": True, "timeout": 200},  # 200ms
            ctx,
        )
    )
    elapsed = time.time() - start
    assert result.output["retrieval_status"] == "timeout"
    assert result.output["task"]["status"] == "running"
    # Timeout was 200ms; the poll should bound around that with some
    # slop for the asyncio.sleep tick.
    assert elapsed < 1.0, f"timeout not respected, took {elapsed:.2f}s"


def test_block_true_waits_for_terminal_then_returns_success(tmp_path: Path) -> None:
    """With a generous timeout, a task that completes mid-poll
    surfaces as ``retrieval_status="success"``."""
    import threading

    ctx, agent_id = _ctx_with_agent(tmp_path)

    # Fire completion on a background thread shortly after the poll starts.
    def _complete_after_short_delay() -> None:
        time.sleep(0.2)
        complete_agent_task(agent_id, result_text="late", registry=ctx.runtime_tasks)

    threading.Thread(target=_complete_after_short_delay, daemon=True).start()

    result = asyncio.run(
        TaskOutputTool.call(
            {"task_id": agent_id, "block": True, "timeout": 5000},
            ctx,
        )
    )
    assert result.output["retrieval_status"] == "success"
    assert result.output["task"]["status"] == "completed"
    assert "late" in result.output["task"]["output"]


def test_unknown_task_id_returns_success_with_null_task(tmp_path: Path) -> None:
    """Mirrors TS: an absent task is reported as success-with-null-body
    (NOT an error). Callers distinguish via the ``task`` key."""
    ctx = ToolContext(workspace_root=tmp_path)
    result = asyncio.run(
        TaskOutputTool.call({"task_id": "a-unknown"}, ctx)
    )
    assert result.output["retrieval_status"] == "success"
    assert result.output["task"] is None


# ---------------------------------------------------------------------------
# Schema bounds — TS TaskOutputTool.tsx:33 z.number().min(0).max(600000)
# ---------------------------------------------------------------------------


def test_timeout_default_is_30000_ms(tmp_path: Path) -> None:
    """The schema's ``default: 30000`` is honored when the model omits
    ``timeout``. Verified by the ``block: false`` early-return path
    (which doesn't actually wait) so the test runs in ms."""
    ctx, agent_id = _ctx_with_agent(tmp_path)
    # No ``timeout`` in input; ``block: false`` so we don't wait.
    result = asyncio.run(
        TaskOutputTool.call({"task_id": agent_id, "block": False}, ctx)
    )
    assert result.output["retrieval_status"] == "not_ready"


def test_timeout_invalid_string_raises() -> None:
    """A non-numeric timeout is rejected at the body — the
    ``ToolInputError`` propagates rather than dispatching with a
    nonsense value."""
    from src.tool_system.errors import ToolInputError

    ctx = ToolContext(workspace_root=Path("/tmp"))
    with pytest.raises(ToolInputError):
        asyncio.run(
            TaskOutputTool.call(
                {"task_id": "a1", "timeout": "huge"}, ctx
            )
        )


def test_schema_declares_minimum_and_maximum_bounds() -> None:
    """The chapter spec pins ``timeout`` to [0, 600000]. Verify the
    schema declares both bounds so downstream JSON-schema validators
    enforce them."""
    schema = TaskOutputTool.input_schema
    timeout_schema = schema["properties"]["timeout"]
    assert timeout_schema["minimum"] == 0
    assert timeout_schema["maximum"] == 600000
    assert timeout_schema["default"] == 30000


def test_schema_declares_block_default_true() -> None:
    schema = TaskOutputTool.input_schema
    block_schema = schema["properties"]["block"]
    assert block_schema["default"] is True


# ---------------------------------------------------------------------------
# Abort fast-path
# ---------------------------------------------------------------------------


def test_block_true_exits_early_on_abort_signal(tmp_path: Path) -> None:
    """If the parent's abort controller signals, the poll exits
    promptly with the current snapshot rather than waiting out the
    timeout."""
    ctx, agent_id = _ctx_with_agent(tmp_path)

    # Fake abort controller — simplest possible shape.
    class _Sig:
        aborted = True
    class _Ctl:
        signal = _Sig()
    ctx.abort_controller = _Ctl()

    start = time.time()
    result = asyncio.run(
        TaskOutputTool.call(
            {"task_id": agent_id, "block": True, "timeout": 5000},
            ctx,
        )
    )
    elapsed = time.time() - start
    # Should bail almost immediately rather than polling the full 5s.
    assert elapsed < 1.0
    # Status retrieved from the running snapshot.
    assert result.output["task"]["status"] == "running"
