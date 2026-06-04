"""Tests for ``src.tasks.progress`` — Chunk C / WI-2.4.

Chapter §"Progress Tracking" arithmetic correctness:
* input tokens are cumulative-per-call → keep latest
* output tokens are per-turn → sum

Plus the ``recent_activities`` cap-5 ring buffer behavior, the
preview-blacklist for SyntheticOutput tools, and the
``ActivityDescriptionResolver`` plumbing.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.tasks.progress import (
    MAX_RECENT_ACTIVITIES,
    AgentProgress,
    ProgressTracker,
    ToolActivity,
    get_progress_update,
    total_tokens_from_tracker,
    update_progress_from_message,
)
from src.types.content_blocks import TextBlock, ToolUseBlock
from src.types.messages import AssistantMessage


def _msg(
    *,
    blocks: list[Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> AssistantMessage:
    """Build a stub AssistantMessage with the supplied usage shape."""
    return AssistantMessage(
        content=blocks or [],
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
    )


# ---------------------------------------------------------------------------
# Token semantics — the chapter's central correctness story
# ---------------------------------------------------------------------------


def test_input_tokens_are_kept_as_latest_not_summed() -> None:
    """input_tokens is cumulative-per-call — every API response includes
    every prior turn's input. Summing double-counts; we keep the latest."""
    tracker = ProgressTracker()
    update_progress_from_message(tracker, _msg(input_tokens=100, output_tokens=10))
    update_progress_from_message(tracker, _msg(input_tokens=200, output_tokens=10))
    update_progress_from_message(tracker, _msg(input_tokens=350, output_tokens=10))
    # Latest input wins — would be 650 if we incorrectly summed.
    assert tracker.latest_input_tokens == 350


def test_output_tokens_are_summed() -> None:
    """output_tokens is per-turn — every response reports only the new
    output for THIS turn. Sum to get the running total."""
    tracker = ProgressTracker()
    update_progress_from_message(tracker, _msg(input_tokens=100, output_tokens=10))
    update_progress_from_message(tracker, _msg(input_tokens=200, output_tokens=20))
    update_progress_from_message(tracker, _msg(input_tokens=300, output_tokens=30))
    assert tracker.cumulative_output_tokens == 60


def test_cache_creation_and_read_tokens_count_toward_input() -> None:
    """The chapter spec adds cache_creation_input_tokens and
    cache_read_input_tokens to the latest input — they are part of the
    cumulative API-billed input shape."""
    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(input_tokens=100, cache_creation=50, cache_read=200, output_tokens=5),
    )
    # 100 + 50 + 200 = 350
    assert tracker.latest_input_tokens == 350
    assert tracker.cumulative_output_tokens == 5


def test_total_tokens_combines_input_and_output() -> None:
    tracker = ProgressTracker()
    update_progress_from_message(tracker, _msg(input_tokens=300, output_tokens=20))
    update_progress_from_message(tracker, _msg(input_tokens=500, output_tokens=40))
    # latest_input=500, cumulative_output=60 → 560
    assert total_tokens_from_tracker(tracker) == 560


def test_message_with_no_usage_is_a_noop_for_tokens() -> None:
    """Some test fixtures and SDK helpers omit usage; the tracker must
    not crash and must not corrupt counters when usage is missing."""
    tracker = ProgressTracker()
    update_progress_from_message(tracker, _msg(input_tokens=100, output_tokens=10))
    # Fabricate a message with no usage (None) — recompute path skips it.
    msg = AssistantMessage(content=[TextBlock(text="x")], usage=None)
    update_progress_from_message(tracker, msg)
    assert tracker.latest_input_tokens == 100
    assert tracker.cumulative_output_tokens == 10


# ---------------------------------------------------------------------------
# Tool-use counting + recent_activities cap
# ---------------------------------------------------------------------------


def test_tool_use_count_increments_per_block() -> None:
    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(blocks=[
            ToolUseBlock(id="t1", name="Read", input={"path": "/a"}),
            ToolUseBlock(id="t2", name="Read", input={"path": "/b"}),
        ]),
    )
    assert tracker.tool_use_count == 2


def test_recent_activities_capped_at_five() -> None:
    """Push 6 tool_use blocks across messages; assert exactly 5 remain."""
    tracker = ProgressTracker()
    for i in range(6):
        update_progress_from_message(
            tracker,
            _msg(blocks=[
                ToolUseBlock(id=f"t{i}", name="Bash", input={"command": f"cmd-{i}"})
            ]),
        )
    assert len(tracker.recent_activities) == MAX_RECENT_ACTIVITIES
    # Oldest dropped; newest preserved.
    assert tracker.recent_activities[-1].input["command"] == "cmd-5"
    assert tracker.recent_activities[0].input["command"] == "cmd-1"


def test_synthetic_output_blocks_omitted_from_preview_but_counted() -> None:
    """``StructuredOutput`` (TS's SYNTHETIC_OUTPUT_TOOL_NAME literal) is
    counted in tool_use_count but excluded from the recent-activities
    preview ring — TS LocalAgentTask.tsx:79-80."""
    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(blocks=[
            ToolUseBlock(id="t1", name="Read", input={"path": "/x"}),
            ToolUseBlock(id="t2", name="StructuredOutput", input={}),
        ]),
    )
    assert tracker.tool_use_count == 2
    assert len(tracker.recent_activities) == 1
    assert tracker.recent_activities[0].tool_name == "Read"


def test_activity_description_resolver_invoked() -> None:
    captured_calls: list[tuple[str, dict]] = []

    def resolver(name: str, input: dict[str, Any]) -> str | None:
        captured_calls.append((name, input))
        return f"Reading {input.get('path', '?')}"

    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(blocks=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})]),
        resolve_activity_description=resolver,
    )
    assert captured_calls == [("Read", {"path": "/x"})]
    assert tracker.recent_activities[0].activity_description == "Reading /x"


def test_resolver_exception_does_not_poison_tracker() -> None:
    """A misbehaving resolver must not crash the tracker; the activity
    is still recorded (without a description)."""
    def resolver(_name: str, _input: dict[str, Any]) -> str | None:
        raise RuntimeError("boom")

    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(blocks=[ToolUseBlock(id="t1", name="Read", input={"path": "/x"})]),
        resolve_activity_description=resolver,
    )
    assert tracker.tool_use_count == 1
    assert tracker.recent_activities[0].activity_description is None


# ---------------------------------------------------------------------------
# get_progress_update projection
# ---------------------------------------------------------------------------


def test_get_progress_update_returns_immutable_snapshot() -> None:
    tracker = ProgressTracker()
    update_progress_from_message(
        tracker,
        _msg(
            blocks=[ToolUseBlock(id="t1", name="Read", input={"path": "/a"})],
            input_tokens=100,
            output_tokens=20,
        ),
    )
    snap1 = get_progress_update(tracker)
    update_progress_from_message(
        tracker,
        _msg(blocks=[ToolUseBlock(id="t2", name="Bash", input={"command": "ls"})]),
    )
    snap2 = get_progress_update(tracker)
    # Snapshot lists are independent.
    assert len(snap1.recent_activities) == 1
    assert len(snap2.recent_activities) == 2
    assert snap1.tool_use_count == 1
    assert snap2.tool_use_count == 2
    # Token count combines latest input + cumulative output.
    assert snap1.token_count == 120


def test_get_progress_update_sets_last_activity() -> None:
    tracker = ProgressTracker()
    assert get_progress_update(tracker).last_activity is None

    update_progress_from_message(
        tracker,
        _msg(blocks=[ToolUseBlock(id="t1", name="Read", input={"path": "/a"})]),
    )
    snap = get_progress_update(tracker)
    assert snap.last_activity is not None
    assert snap.last_activity.tool_name == "Read"


# ---------------------------------------------------------------------------
# AgentProgress dataclass shape
# ---------------------------------------------------------------------------


def test_agent_progress_default_construction() -> None:
    p = AgentProgress()
    assert p.tool_use_count == 0
    assert p.token_count == 0
    assert p.last_activity is None
    assert p.recent_activities == []
    assert p.summary is None


def test_tool_activity_dataclass_fields() -> None:
    """Mirrors LocalAgentTask.tsx:23-32 shape."""
    a = ToolActivity(tool_name="Read", input={"path": "/x"})
    assert a.tool_name == "Read"
    assert a.input == {"path": "/x"}
    assert a.activity_description is None
    assert a.is_search is None
    assert a.is_read is None
