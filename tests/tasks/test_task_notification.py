"""WI-3.1 + WI-3.2 tests — task-notification XML envelope + dedup.

Covers the chapter's notification surface: byte-for-byte envelope
parity with TS, ``notified`` flag check-and-set, idempotent enqueue,
and the racy "completion fires between two sweeps" scenario.
"""
from __future__ import annotations

import time

import pytest

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_agent import (
    LocalAgentTaskState,
    register_async_agent,
)
from src.tasks_core import generate_task_id
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    drain_pending_notifications,
    peek_pending_notifications,
)
from src.utils.task_notification import (
    build_task_notification_xml,
    enqueue_agent_notification,
)


@pytest.fixture(autouse=True)
def _clear_queue():
    """Each test starts with an empty global notification queue."""
    clear_pending_notifications()
    yield
    clear_pending_notifications()


# ---------------------------------------------------------------------------
# build_task_notification_xml — envelope shape parity with TS
# ---------------------------------------------------------------------------


def test_envelope_contains_all_chapter_required_tags() -> None:
    """Chapter §"Background: Three Channels" lists the fields:
    task-id, tool-use-id, output-file, status, summary, result, usage.
    Snapshot the envelope and assert each tag appears."""
    xml = build_task_notification_xml(
        task_id="a1234567z",
        description="hello",
        status="completed",
        output_file="/tmp/a1234567z.jsonl",
        final_message="Done — 3 files updated",
        usage={"total_tokens": 1500, "tool_uses": 8, "duration_ms": 12000},
        tool_use_id="toolu_abc",
    )
    for tag in [
        "<task-notification>",
        "<task-id>a1234567z</task-id>",
        "<tool-use-id>toolu_abc</tool-use-id>",
        "<output-file>/tmp/a1234567z.jsonl</output-file>",
        "<status>completed</status>",
        "<summary>",
        "<result>Done — 3 files updated</result>",
        "<usage>",
        "<total_tokens>1500</total_tokens>",
        "<tool_uses>8</tool_uses>",
        "<duration_ms>12000</duration_ms>",
        "</usage>",
        "</task-notification>",
    ]:
        assert tag in xml, f"missing {tag!r} in:\n{xml}"


def test_envelope_summary_phrasing_completed() -> None:
    xml = build_task_notification_xml(
        task_id="a1", description="my agent", status="completed",
        output_file="/x",
    )
    assert '<summary>Agent "my agent" completed</summary>' in xml


def test_envelope_summary_phrasing_failed_with_error() -> None:
    xml = build_task_notification_xml(
        task_id="a1", description="my agent", status="failed",
        error="ValueError: bad", output_file="/x",
    )
    assert 'failed: ValueError: bad' in xml


def test_envelope_summary_phrasing_failed_no_error() -> None:
    xml = build_task_notification_xml(
        task_id="a1", description="my agent", status="failed",
        output_file="/x",
    )
    assert "failed: Unknown error" in xml


def test_envelope_summary_phrasing_killed() -> None:
    xml = build_task_notification_xml(
        task_id="a1", description="my agent", status="killed",
        output_file="/x",
    )
    assert '<summary>Agent "my agent" was stopped</summary>' in xml


def test_envelope_optional_sections_omitted_when_inputs_absent() -> None:
    xml = build_task_notification_xml(
        task_id="a1", description="x", status="completed",
        output_file="/x",
    )
    # Without final_message, no <result> tag.
    assert "<result>" not in xml
    # Without usage, no <usage> block.
    assert "<usage>" not in xml
    # Without tool_use_id, no <tool-use-id> tag.
    assert "<tool-use-id>" not in xml


def test_envelope_snapshot_completed_with_usage() -> None:
    """Snapshot test — pin byte-level shape so future edits surface
    obvious diffs at review time. Updating this snapshot is a
    deliberate review step.

    This branch covers the no-tool-use-id form (the Agent-tool spawn
    case where the parent's ``tool_use_id`` isn't threaded through —
    e.g. a coordinator that spawned without binding the response to a
    specific tool_use). Companion test below covers the with-tool-use-id
    branch (per critic concern N2)."""
    xml = build_task_notification_xml(
        task_id="a1234567z",
        description="snapshot",
        status="completed",
        output_file="/tmp/a1234567z.jsonl",
        final_message="ok",
        usage={"total_tokens": 100, "tool_uses": 2, "duration_ms": 1000},
    )
    expected = (
        "<task-notification>\n"
        "<task-id>a1234567z</task-id>\n"
        "<output-file>/tmp/a1234567z.jsonl</output-file>\n"
        "<status>completed</status>\n"
        '<summary>Agent "snapshot" completed</summary>'
        "\n<result>ok</result>"
        "\n<usage><total_tokens>100</total_tokens><tool_uses>2</tool_uses><duration_ms>1000</duration_ms></usage>\n"
        "</task-notification>"
    )
    assert xml == expected, f"snapshot drifted:\n{xml!r}\nexpected:\n{expected!r}"


def test_envelope_snapshot_with_tool_use_id() -> None:
    """Critic N2 fold-in (Chunk E): the with-``tool-use-id`` branch
    inserts ``\\n<tool-use-id>...</tool-use-id>`` directly after the
    ``<task-id>`` close tag. Pin the shape so a future edit that
    moves or restyles the insertion shows up in review."""
    xml = build_task_notification_xml(
        task_id="a1234567z",
        description="snapshot",
        status="completed",
        output_file="/tmp/a1234567z.jsonl",
        final_message="ok",
        tool_use_id="toolu_abc123",
    )
    expected = (
        "<task-notification>\n"
        "<task-id>a1234567z</task-id>\n"
        "<tool-use-id>toolu_abc123</tool-use-id>\n"
        "<output-file>/tmp/a1234567z.jsonl</output-file>\n"
        "<status>completed</status>\n"
        '<summary>Agent "snapshot" completed</summary>'
        "\n<result>ok</result>\n"
        "</task-notification>"
    )
    assert xml == expected, f"snapshot drifted:\n{xml!r}\nexpected:\n{expected!r}"


# ---------------------------------------------------------------------------
# enqueue_agent_notification — WI-3.2 check-and-set against duplicates
# ---------------------------------------------------------------------------


def _make_running(reg: RuntimeTaskRegistry) -> LocalAgentTaskState:
    agent_id = generate_task_id("local_agent")
    register_async_agent(
        agent_id=agent_id, description="example", prompt="x",
        agent_type="general-purpose", registry=reg,
    )
    return reg.get(agent_id)


def test_enqueue_marks_task_notified_and_pushes_one_envelope() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_running(reg)

    fired = enqueue_agent_notification(
        task_id=state.id,
        description=state.description,
        status="completed",
        output_file=state.output_file,
        registry=reg,
    )

    assert fired is True
    assert reg.get(state.id).notified is True
    queue = peek_pending_notifications()
    assert len(queue) == 1
    assert "<task-notification>" in queue[0].value
    assert queue[0].mode == "task-notification"


def test_enqueue_is_no_op_when_already_notified() -> None:
    """Idempotency contract: a second call against the same task does
    NOT push a second envelope."""
    reg = RuntimeTaskRegistry()
    state = _make_running(reg)

    enqueue_agent_notification(
        task_id=state.id, description=state.description,
        status="completed", output_file=state.output_file, registry=reg,
    )
    fired_again = enqueue_agent_notification(
        task_id=state.id, description=state.description,
        status="killed", output_file=state.output_file, registry=reg,
    )
    assert fired_again is False
    assert len(peek_pending_notifications()) == 1


def test_concurrent_completion_and_kill_produces_one_envelope() -> None:
    """Race scenario from chapter: completion and kill fire close
    together. The atomic check-and-set on ``notified`` ensures exactly
    one envelope reaches the queue."""
    import threading

    reg = RuntimeTaskRegistry()
    state = _make_running(reg)
    barrier = threading.Barrier(2)

    def emit(status: str) -> None:
        barrier.wait()  # both threads enter at the same instant
        enqueue_agent_notification(
            task_id=state.id, description=state.description,
            status=status, output_file=state.output_file, registry=reg,
        )

    t1 = threading.Thread(target=emit, args=("completed",))
    t2 = threading.Thread(target=emit, args=("killed",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    queue = peek_pending_notifications()
    assert len(queue) == 1, f"race produced {len(queue)} envelopes — dedup failed"


def test_drain_removes_envelopes_from_queue() -> None:
    reg = RuntimeTaskRegistry()
    a = _make_running(reg)
    b = _make_running(reg)
    enqueue_agent_notification(
        task_id=a.id, description=a.description,
        status="completed", output_file=a.output_file, registry=reg,
    )
    enqueue_agent_notification(
        task_id=b.id, description=b.description,
        status="failed", output_file=b.output_file, registry=reg,
    )
    drained = drain_pending_notifications()
    assert len(drained) == 2
    assert peek_pending_notifications() == []
