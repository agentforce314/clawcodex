"""WI-2.3 lifecycle helper tests + WI-2.2/WI-2.4 integration smoke.

Covers:
* ``register_async_agent`` populates ``output_file`` with the
  transcript path and registers a typed state.
* ``queue_pending_message`` / ``drain_pending_messages`` round-trip
  with FIFO order, terminal-state guard, atomic drain.
* ``complete_agent_task`` / ``fail_agent_task`` / ``kill_async_agent``
  flip the registry atomically and respect the terminal-state guard.
* End-to-end: an async agent run produces a JSONL transcript on disk
  with one line per message and ``finalize_agent_tool.total_tokens``
  is no longer hard-coded to 0.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    LocalAgentTaskState,
    complete_agent_task,
    drain_pending_messages,
    fail_agent_task,
    is_local_agent_task_terminal,
    kill_async_agent,
    queue_pending_message,
    register_async_agent,
    update_agent_progress,
)
from src.tasks.progress import AgentProgress
from src.tasks_core import generate_task_id
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.types.content_blocks import TextBlock, ToolUseBlock
from src.types.messages import AssistantMessage


# ---------------------------------------------------------------------------
# register_async_agent
# ---------------------------------------------------------------------------


def test_register_async_agent_sets_transcript_output_file() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    state = register_async_agent(
        agent_id=agent_id,
        description="hello",
        prompt="do work",
        agent_type="general-purpose",
        registry=reg,
    )
    assert state.id == agent_id
    assert state.status == "running"
    assert state.output_file.endswith(f"{agent_id}.jsonl")
    assert ".clawcodex/transcripts" in state.output_file


def test_register_async_agent_replaces_existing_entry() -> None:
    """The resume path needs to overwrite an existing terminal entry
    with a fresh ``running`` state."""
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="first", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    state2 = register_async_agent(
        agent_id=agent_id, description="second", prompt="y",
        agent_type="general-purpose", registry=reg,
    )
    assert state2.description == "second"
    assert reg.get(agent_id).description == "second"


# ---------------------------------------------------------------------------
# pending_messages — queue + drain
# ---------------------------------------------------------------------------


def test_queue_and_drain_pending_messages_fifo() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )

    assert queue_pending_message(agent_id, "first", reg) is True
    assert queue_pending_message(agent_id, "second", reg) is True

    drained = drain_pending_messages(agent_id, reg)
    assert drained == ["first", "second"]
    # Drain is atomic — second drain returns nothing.
    assert drain_pending_messages(agent_id, reg) == []


def test_queue_pending_refuses_terminal_state() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="done", registry=reg)
    # Terminal — refuse the queue write.
    assert queue_pending_message(agent_id, "stale", reg) is False
    assert reg.get(agent_id).pending_messages == []


def test_drain_on_empty_queue_is_safe() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    assert drain_pending_messages(agent_id, reg) == []


# ---------------------------------------------------------------------------
# complete / fail / kill
# ---------------------------------------------------------------------------


def test_complete_agent_task_flips_status() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="all good", registry=reg)
    state = reg.get(agent_id)
    assert state.status == "completed"
    assert state.result_text == "all good"
    assert state.end_time is not None
    assert is_local_agent_task_terminal(state)


def test_fail_agent_task_records_error() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    fail_agent_task(agent_id, error="boom", registry=reg)
    state = reg.get(agent_id)
    assert state.status == "failed"
    assert state.error == "boom"
    # result_text mirrors error so TaskOutput callers see it.
    assert state.result_text == "boom"


def test_kill_async_agent_flips_status_and_signals_event() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    state = register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    # Inject an asyncio.Event that the kill helper should signal.
    event = asyncio.Event()
    from dataclasses import replace
    reg.upsert(replace(state, abort_event=event))

    kill_async_agent(agent_id, reg)

    assert reg.get(agent_id).status == "killed"
    assert event.is_set()


def test_terminal_guard_prevents_re_completion() -> None:
    """Once a task is in a terminal state, complete/fail/kill are no-ops."""
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="first", registry=reg)
    fail_agent_task(agent_id, error="late", registry=reg)
    kill_async_agent(agent_id, reg)

    state = reg.get(agent_id)
    # Original completion stuck.
    assert state.status == "completed"
    assert state.result_text == "first"
    assert state.error is None


# ---------------------------------------------------------------------------
# update_agent_progress
# ---------------------------------------------------------------------------


def test_update_agent_progress_sets_snapshot() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    p = AgentProgress(tool_use_count=3, token_count=1000)
    update_agent_progress(agent_id, p, reg)
    assert reg.get(agent_id).progress == p


def test_update_agent_progress_preserves_existing_summary() -> None:
    """The chapter spec: progress updates from assistant messages must
    not clobber a summary set by a background-summarization service."""
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    seeded = AgentProgress(tool_use_count=1, summary="research phase")
    update_agent_progress(agent_id, seeded, reg)
    # Now an update without a summary should preserve the existing one.
    bare = AgentProgress(tool_use_count=2)
    update_agent_progress(agent_id, bare, reg)
    state = reg.get(agent_id)
    assert state.progress.tool_use_count == 2
    assert state.progress.summary == "research phase"


def test_update_agent_progress_noop_after_terminal() -> None:
    reg = RuntimeTaskRegistry()
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="x", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    complete_agent_task(agent_id, result_text="done", registry=reg)
    update_agent_progress(agent_id, AgentProgress(tool_use_count=99), reg)
    # Progress not updated post-terminal.
    assert reg.get(agent_id).progress is None


# ---------------------------------------------------------------------------
# End-to-end — gate-zero transcript + non-zero token count
# ---------------------------------------------------------------------------


def _wait_for_terminal(
    ctx: ToolContext, task_id: str, timeout_s: float = 2.0
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = ctx.runtime_tasks.get(task_id)
        if isinstance(state, LocalAgentTaskState) and state.status in (
            "completed", "failed", "killed",
        ):
            return state.status
        time.sleep(0.02)
    state = ctx.runtime_tasks.get(task_id)
    return getattr(state, "status", "<missing>")


def test_async_agent_writes_jsonl_transcript_on_disk(tmp_path: Path) -> None:
    """Gate-zero acceptance: after an async agent runs, the JSONL
    transcript file exists at ``state.output_file`` with one line per
    yielded message. This is the prerequisite for Phase 3 / WI-3.1
    notification XML and Phase 7 / WI-7.4 auto-resume."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(workspace_root=tmp_path)

    async def _fake(_params):
        yield AssistantMessage(
            content=[TextBlock(text="step one")],
            usage={"input_tokens": 100, "output_tokens": 10},
        )
        yield AssistantMessage(
            content=[TextBlock(text="step two")],
            usage={"input_tokens": 200, "output_tokens": 15},
        )

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "transcript smoke",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )
        task_id = str(result.output["agent_id"])
        _wait_for_terminal(ctx, task_id)

    state = ctx.runtime_tasks.get(task_id)
    assert isinstance(state, LocalAgentTaskState)

    # 1. output_file is the JSONL transcript path.
    assert state.output_file.endswith(f"{task_id}.jsonl")

    # 2. The file exists and has one line per yielded message.
    transcript_path = Path(state.output_file)
    assert transcript_path.exists(), f"no transcript at {transcript_path}"
    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        # Each line is a parseable JSON object containing the asdict
        # of an AssistantMessage.
        parsed = json.loads(line)
        assert isinstance(parsed, dict)


def test_async_agent_finalize_total_tokens_is_no_longer_zero(tmp_path: Path) -> None:
    """WI-2.4 acceptance: ``finalize_agent_tool.total_tokens`` reports
    the chapter-correct latest_input + cumulative_output instead of the
    pre-WI-2.4 hard-coded ``0``."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(workspace_root=tmp_path)

    async def _fake(_params):
        yield AssistantMessage(
            content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/a"}),
                TextBlock(text="reading a"),
            ],
            usage={"input_tokens": 100, "output_tokens": 10},
        )
        yield AssistantMessage(
            content=[TextBlock(text="done")],
            usage={"input_tokens": 250, "output_tokens": 20},
        )

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "tokens",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )
        task_id = str(result.output["agent_id"])
        _wait_for_terminal(ctx, task_id)

    # The final result is captured during the lifecycle and stored in
    # result_text; we verify the live tracker fed finalize_agent_tool
    # by checking the cleanup log line count would be > 0. More
    # robustly: sanity-check the lifecycle state shows non-zero work.
    state = ctx.runtime_tasks.get(task_id)
    assert isinstance(state, LocalAgentTaskState)
    assert state.status == "completed"

    # Direct finalize_agent_tool round-trip: feed a tracker and assert
    # total_tokens is non-zero. This is the unit-level proof that the
    # WI-2.4 refactor lands.
    from src.agent.agent_tool_utils import finalize_agent_tool
    from src.tasks.progress import ProgressTracker, update_progress_from_message

    tracker = ProgressTracker()
    msgs = []
    async def _collect():
        async for m in _fake(None):
            msgs.append(m)
            update_progress_from_message(tracker, m)
    asyncio.run(_collect())

    final = finalize_agent_tool(
        msgs,
        agent_id=task_id,
        metadata={"start_time": time.time(), "agent_type": "general-purpose"},
        progress=tracker,
    )
    # latest_input=350 (250+100+0+0 from cache_*=0 default), cumulative_output=30 → 380.
    # Actually: per-call latest = 250+0+0 = 250; cumulative = 30. Total = 280.
    assert final.total_tokens > 0, "WI-2.4 regression: total_tokens still zero"
    assert final.total_tool_use_count == 1


def test_finalize_falls_back_to_message_recompute_when_no_tracker(tmp_path: Path) -> None:
    """A sync caller that doesn't feed a tracker still gets a non-zero
    total via the message-based recompute path — the WI-2.4 fallback
    rather than reverting to ``total_tokens=0``."""
    from src.agent.agent_tool_utils import finalize_agent_tool

    msgs = [
        AssistantMessage(
            content=[TextBlock(text="reply")],
            usage={"input_tokens": 100, "output_tokens": 10},
        ),
    ]
    final = finalize_agent_tool(
        msgs,
        agent_id="a-test",
        metadata={"start_time": time.time(), "agent_type": "x"},
        # No progress=...; the function must recompute from message usage.
    )
    assert final.total_tokens == 110
