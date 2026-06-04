"""WI-6.2 + sub-WI-6.2.a tests — InProcessTeammateTaskState + abort hooks."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from src.task_registry import RuntimeTaskRegistry, get_task_by_type
from src.tasks.in_process_teammate import (
    CurrentWorkAbortedError,
    InProcessTeammateTask,
    InProcessTeammateTaskState,
    TEAMMATE_MESSAGES_UI_CAP,
    TeammateAbortedError,
    TeammateIdentity,
    append_capped_message,
    check_abort_events,
    is_in_process_teammate_task,
    outer_lifecycle_should_catch,
    run_with_two_level_abort,
)
from src.tasks_core import is_terminal_task_status


def _make_running_state(task_id: str = "t1abc1234") -> InProcessTeammateTaskState:
    return InProcessTeammateTaskState(
        id=task_id,
        type="in_process_teammate",
        status="running",
        description="x",
        start_time=0.0,
        output_file="/tmp/x",
        identity=TeammateIdentity(
            agent_id=task_id, agent_name="alice", team_name="my-team",
        ),
        prompt="x",
        abort_event=asyncio.Event(),
        current_work_abort_event=asyncio.Event(),
    )


# ---------------------------------------------------------------------------
# State construction + chapter field accounting
# ---------------------------------------------------------------------------


def test_chapter_required_fields_present() -> None:
    state = _make_running_state()
    assert state.identity.agent_id == "t1abc1234"
    assert state.identity.agent_name == "alice"
    assert state.identity.team_name == "my-team"
    assert state.identity.plan_mode_required is False
    # Two-level abort signals
    assert isinstance(state.abort_event, asyncio.Event)
    assert isinstance(state.current_work_abort_event, asyncio.Event)
    # Lifecycle flags
    assert state.is_idle is False
    assert state.shutdown_requested is False
    assert state.awaiting_plan_approval is False
    assert state.permission_mode == "default"
    # Inboxes
    assert state.pending_user_messages == []
    assert state.in_progress_tool_use_ids == set()
    # Progress / message UI fields
    assert state.last_reported_tool_count == 0
    assert state.last_reported_token_count == 0
    assert state.messages == []


def test_is_in_process_teammate_task_predicate() -> None:
    state = _make_running_state()
    assert is_in_process_teammate_task(state) is True
    assert is_in_process_teammate_task("not a state") is False
    assert is_in_process_teammate_task(None) is False


# ---------------------------------------------------------------------------
# 50-message UI cap (whale-session OOM guard)
# ---------------------------------------------------------------------------


def test_messages_ui_cap_drops_oldest_on_overflow() -> None:
    capped = list(range(TEAMMATE_MESSAGES_UI_CAP))
    new = append_capped_message(capped, TEAMMATE_MESSAGES_UI_CAP)  # one over
    assert len(new) == TEAMMATE_MESSAGES_UI_CAP
    assert new[-1] == TEAMMATE_MESSAGES_UI_CAP
    assert new[0] == 1  # 0 dropped


def test_append_capped_to_empty() -> None:
    assert append_capped_message(None, "x") == ["x"]
    assert append_capped_message([], "x") == ["x"]


def test_append_capped_below_cap_grows() -> None:
    out = append_capped_message([1, 2, 3], 4)
    assert out == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Two-level abort — exception classes (sub-WI-6.2.a)
# ---------------------------------------------------------------------------


def test_two_level_exception_classes_subclass_cancelled_error() -> None:
    """Both subclass ``asyncio.CancelledError`` so existing
    cancellation paths keep working without special-casing."""
    assert issubclass(TeammateAbortedError, asyncio.CancelledError)
    assert issubclass(CurrentWorkAbortedError, asyncio.CancelledError)
    # And they're distinct.
    assert TeammateAbortedError is not CurrentWorkAbortedError


def test_check_abort_events_no_op_when_neither_set() -> None:
    state = _make_running_state()
    # No exception raised.
    check_abort_events(state)


def test_check_abort_events_raises_current_work_when_set() -> None:
    state = _make_running_state()
    state.current_work_abort_event.set()
    with pytest.raises(CurrentWorkAbortedError):
        check_abort_events(state)


def test_check_abort_events_raises_teammate_when_kill_set() -> None:
    state = _make_running_state()
    state.abort_event.set()
    with pytest.raises(TeammateAbortedError):
        check_abort_events(state)


def test_check_abort_events_kill_wins_when_both_set() -> None:
    """Both events fired → TeammateAbortedError takes precedence
    (kill is the stronger intent)."""
    state = _make_running_state()
    state.abort_event.set()
    state.current_work_abort_event.set()
    with pytest.raises(TeammateAbortedError):
        check_abort_events(state)


# ---------------------------------------------------------------------------
# Outer-lifecycle catch policy
# ---------------------------------------------------------------------------


def test_outer_lifecycle_catches_current_work() -> None:
    """Redirect pattern — outer loop catches CurrentWorkAbortedError."""
    assert outer_lifecycle_should_catch(CurrentWorkAbortedError("x")) is True


def test_outer_lifecycle_does_not_catch_teammate_aborted() -> None:
    """Kill pattern — outer loop does NOT catch."""
    assert outer_lifecycle_should_catch(TeammateAbortedError("x")) is False


def test_outer_lifecycle_does_not_catch_plain_cancelled() -> None:
    """Plain CancelledError (e.g. asyncio.Task.cancel()) propagates;
    the teammate respects the cancel."""
    assert outer_lifecycle_should_catch(asyncio.CancelledError()) is False


# ---------------------------------------------------------------------------
# run_with_two_level_abort — integration helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_two_level_abort_completes_normally() -> None:
    state = _make_running_state()
    counter = {"n": 0}

    async def work() -> str:
        for _ in range(3):
            await asyncio.sleep(0.01)
            counter["n"] += 1
        return "done"

    result = await run_with_two_level_abort(work, state_provider=lambda: state)
    assert result == "done"
    assert counter["n"] == 3


@pytest.mark.asyncio
async def test_run_with_two_level_abort_redirect_raises_current_work() -> None:
    state = _make_running_state()

    async def work() -> str:
        await asyncio.sleep(2.0)
        return "should not reach"

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        state.current_work_abort_event.set()

    with pytest.raises(CurrentWorkAbortedError):
        await asyncio.gather(
            run_with_two_level_abort(work, state_provider=lambda: state),
            trigger(),
        )


@pytest.mark.asyncio
async def test_run_with_two_level_abort_kill_raises_teammate() -> None:
    state = _make_running_state()

    async def work() -> str:
        await asyncio.sleep(2.0)
        return "should not reach"

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        state.abort_event.set()

    with pytest.raises(TeammateAbortedError):
        await asyncio.gather(
            run_with_two_level_abort(work, state_provider=lambda: state),
            trigger(),
        )


# ---------------------------------------------------------------------------
# Task adapter — kill flips status and signals abort
# ---------------------------------------------------------------------------


def test_task_adapter_registered_for_in_process_teammate() -> None:
    """Centralized ``register_task`` in ``tasks/__init__.py`` registers
    InProcessTeammateTask alongside LocalShellTask and LocalAgentTask."""
    impl = get_task_by_type("in_process_teammate")
    assert impl is not None
    assert impl.name == "InProcessTeammateTask"
    assert impl.type == "in_process_teammate"


@pytest.mark.asyncio
async def test_kill_flips_status_and_signals_abort_event() -> None:
    reg = RuntimeTaskRegistry()
    state = _make_running_state()
    reg.upsert(state)

    await InProcessTeammateTask().kill(state.id, reg)

    refreshed = reg.get(state.id)
    assert isinstance(refreshed, InProcessTeammateTaskState)
    assert refreshed.status == "killed"
    assert is_terminal_task_status(refreshed.status)
    # Abort event fired so the run loop sees TeammateAbortedError on
    # its next yield.
    assert refreshed.abort_event.is_set()


@pytest.mark.asyncio
async def test_kill_terminal_state_is_noop() -> None:
    """Already-terminal teammate is unchanged by kill (idempotent)."""
    reg = RuntimeTaskRegistry()
    state = _make_running_state()
    state = replace(state, status="completed")
    reg.upsert(state)

    await InProcessTeammateTask().kill(state.id, reg)

    refreshed = reg.get(state.id)
    assert refreshed.status == "completed"
    # abort_event was NOT signalled (no kill needed).
    assert not refreshed.abort_event.is_set()
