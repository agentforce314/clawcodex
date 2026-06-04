"""WI-3.3 tests — pending_messages drain at tool-round boundary.

The chapter contract: messages enqueued via ``queue_pending_message``
arrive at the next tool-round boundary, NOT mid-execution. The query
loop's between-turn hook is what surfaces them in the conversation.
"""
from __future__ import annotations

from src.query.query import _drain_pending_user_messages
from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    queue_pending_message,
    register_async_agent,
)
from src.tasks_core import generate_task_id
from src.tool_system.context import ToolContext
from src.types.messages import UserMessage


def _make_context_with_running_agent(tmp_path) -> tuple[ToolContext, str]:
    ctx = ToolContext(workspace_root=tmp_path)
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=ctx.runtime_tasks,
    )
    ctx.agent_id = agent_id
    return ctx, agent_id


def test_drain_returns_empty_when_no_pending(tmp_path) -> None:
    ctx, _ = _make_context_with_running_agent(tmp_path)
    drained = _drain_pending_user_messages(ctx)
    assert drained == []


def test_drain_returns_user_messages_in_fifo_order(tmp_path) -> None:
    ctx, agent_id = _make_context_with_running_agent(tmp_path)
    queue_pending_message(agent_id, "first", ctx.runtime_tasks)
    queue_pending_message(agent_id, "second", ctx.runtime_tasks)
    queue_pending_message(agent_id, "third", ctx.runtime_tasks)

    drained = _drain_pending_user_messages(ctx)

    assert len(drained) == 3
    assert all(isinstance(m, UserMessage) for m in drained)
    assert [m.content for m in drained] == ["first", "second", "third"]


def test_drain_clears_inbox_atomically(tmp_path) -> None:
    """Second drain returns an empty list — the inbox is consumed in
    one atomic sweep at the boundary."""
    ctx, agent_id = _make_context_with_running_agent(tmp_path)
    queue_pending_message(agent_id, "only-once", ctx.runtime_tasks)

    first = _drain_pending_user_messages(ctx)
    second = _drain_pending_user_messages(ctx)

    assert len(first) == 1
    assert second == []


def test_drain_noop_when_no_agent_id(tmp_path) -> None:
    """The hook is per-agent — without ``context.agent_id`` (top-level
    or main-session calls) there's nothing to drain."""
    ctx = ToolContext(workspace_root=tmp_path)
    # ctx.agent_id stays None
    drained = _drain_pending_user_messages(ctx)
    assert drained == []


def test_drain_noop_when_agent_id_unknown(tmp_path) -> None:
    """An ``agent_id`` that isn't registered in runtime_tasks must not
    crash the query loop — the drain returns empty."""
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.agent_id = "a-unknown"
    drained = _drain_pending_user_messages(ctx)
    assert drained == []


def test_drain_refuses_terminal_state_implicitly(tmp_path) -> None:
    """When the lifecycle has flipped the task to terminal, queueing
    refuses (covered in lifecycle tests). Drain on a terminal entry
    returns empty since pending_messages stayed empty."""
    from src.tasks.local_agent import complete_agent_task

    ctx, agent_id = _make_context_with_running_agent(tmp_path)
    complete_agent_task(agent_id, result_text="done", registry=ctx.runtime_tasks)

    # Queue refuses post-terminal — the inbox stays empty.
    fired = queue_pending_message(agent_id, "stale", ctx.runtime_tasks)
    assert fired is False
    assert _drain_pending_user_messages(ctx) == []
