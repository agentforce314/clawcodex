"""Tests for Phase-11 FrameEvent observability."""

from __future__ import annotations

import time

import pytest

from src.tui.frame_metrics import (
    FRAME_DEBUG_ENV,
    FrameEvent,
    TimedPhase,
    clear_observers_for_tests,
    emit_frame_event,
    is_enabled,
    register_frame_observer,
)


@pytest.fixture(autouse=True)
def _reset_observers():
    clear_observers_for_tests()
    yield
    clear_observers_for_tests()


# ------------------------------------------------------------------
# is_enabled
# ------------------------------------------------------------------


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FRAME_DEBUG_ENV, raising=False)
    assert is_enabled() is False


def test_enabled_when_env_var_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    assert is_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_value_one_enables(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, value)
    assert is_enabled() is False


# ------------------------------------------------------------------
# emit_frame_event
# ------------------------------------------------------------------


def test_disabled_emit_does_not_invoke_observers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FRAME_DEBUG_ENV, raising=False)
    fired: list[FrameEvent] = []
    register_frame_observer(fired.append)
    emit_frame_event(FrameEvent(duration_ms=10.0))
    assert fired == []


def test_enabled_emit_notifies_all_observers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received: list[FrameEvent] = []
    register_frame_observer(received.append)

    event = FrameEvent(
        duration_ms=12.5, phases={"render": 5.2, "diff": 1.1}
    )
    emit_frame_event(event)
    assert received == [event]


def test_multiple_observers_all_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received_a: list[FrameEvent] = []
    received_b: list[FrameEvent] = []
    register_frame_observer(received_a.append)
    register_frame_observer(received_b.append)
    emit_frame_event(FrameEvent(duration_ms=1.0))
    assert len(received_a) == 1
    assert len(received_b) == 1


# ------------------------------------------------------------------
# unregister
# ------------------------------------------------------------------


def test_unregister_stops_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received: list[FrameEvent] = []
    unreg = register_frame_observer(received.append)
    emit_frame_event(FrameEvent(duration_ms=1.0))
    assert len(received) == 1
    unreg()
    emit_frame_event(FrameEvent(duration_ms=2.0))
    assert len(received) == 1


def test_unregister_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    unreg = register_frame_observer(lambda e: None)
    unreg()
    unreg()  # No-op; must not raise.


# ------------------------------------------------------------------
# TimedPhase
# ------------------------------------------------------------------


def test_timed_phase_records_into_dict() -> None:
    phases: dict[str, float] = {}
    with TimedPhase(phases, "render"):
        time.sleep(0.001)
    assert "render" in phases
    assert phases["render"] >= 0  # ms


def test_timed_phase_accumulates_when_called_twice() -> None:
    phases: dict[str, float] = {}
    with TimedPhase(phases, "render"):
        time.sleep(0.001)
    first = phases["render"]
    with TimedPhase(phases, "render"):
        time.sleep(0.001)
    assert phases["render"] >= first  # accumulated, not overwritten


def test_timed_phase_records_even_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The context manager itself is observability-agnostic — it just
    measures. Whether the FrameEvent fires is decided by the caller."""

    monkeypatch.delenv(FRAME_DEBUG_ENV, raising=False)
    phases: dict[str, float] = {}
    with TimedPhase(phases, "x"):
        pass
    assert "x" in phases


# ------------------------------------------------------------------
# Frame event shape
# ------------------------------------------------------------------


def test_frame_event_defaults_are_sensible() -> None:
    event = FrameEvent(duration_ms=10.0)
    assert event.phases == {}
    assert event.component_attribution is None
    assert event.yoga_visited == 0
    assert event.yoga_measured == 0
    assert event.flickers == ()


def test_frame_event_is_frozen() -> None:
    event = FrameEvent(duration_ms=10.0)
    with pytest.raises(Exception):
        event.duration_ms = 20.0  # type: ignore[misc]


def test_observer_exceptions_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented behavior: a misbehaving observer is the caller's
    problem; we don't swallow."""

    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")

    def bad_observer(_event: FrameEvent) -> None:
        raise RuntimeError("simulated observer bug")

    register_frame_observer(bad_observer)
    with pytest.raises(RuntimeError):
        emit_frame_event(FrameEvent(duration_ms=1.0))
