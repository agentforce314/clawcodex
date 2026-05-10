"""Tests for the Phase-11 close-out: FrameEvent emission from app lifecycle.

Verifies the helper ``ClawCodexTUI._emit_lifecycle_frame`` actually
posts FrameEvents to the singleton observer when
``CLAWCODEX_DEBUG_REPAINTS=1``, and that the emission stays no-op
(no observer notification) when disabled.
"""

from __future__ import annotations

import pytest

from src.tui.frame_metrics import (
    FRAME_DEBUG_ENV,
    FrameEvent,
    clear_observers_for_tests,
    register_frame_observer,
)


@pytest.fixture(autouse=True)
def _reset_observers():
    clear_observers_for_tests()
    yield
    clear_observers_for_tests()


def test_emit_lifecycle_frame_disabled_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No observer fires when the env var is unset."""

    monkeypatch.delenv(FRAME_DEBUG_ENV, raising=False)
    received: list[FrameEvent] = []
    register_frame_observer(received.append)

    # Build a minimal stub of the app helper without constructing the full app.
    from src.tui.app import ClawCodexTUI

    # Use ``__new__`` to bypass ``__init__`` (which needs a provider).
    app = ClawCodexTUI.__new__(ClawCodexTUI)
    app._emit_lifecycle_frame("test.label", 1.5)
    assert received == []


def test_emit_lifecycle_frame_enabled_fires_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var is set, observers receive a FrameEvent shaped per-spec."""

    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received: list[FrameEvent] = []
    register_frame_observer(received.append)

    from src.tui.app import ClawCodexTUI

    app = ClawCodexTUI.__new__(ClawCodexTUI)
    app._emit_lifecycle_frame(
        "ClawCodexTUI.on_mount",
        4.2,
        phases={"mount": 4.2},
    )

    assert len(received) == 1
    event = received[0]
    assert event.duration_ms == 4.2
    assert event.component_attribution == "ClawCodexTUI.on_mount"
    assert event.phases == {"mount": 4.2}


def test_emit_lifecycle_frame_default_phases_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received: list[FrameEvent] = []
    register_frame_observer(received.append)
    from src.tui.app import ClawCodexTUI

    app = ClawCodexTUI.__new__(ClawCodexTUI)
    app._emit_lifecycle_frame("x", 1.0)
    assert received[0].phases == {}


def test_emit_lifecycle_frame_phases_dict_is_copied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper copies the phases dict so the caller can mutate
    afterwards without affecting the emitted event."""

    monkeypatch.setenv(FRAME_DEBUG_ENV, "1")
    received: list[FrameEvent] = []
    register_frame_observer(received.append)
    from src.tui.app import ClawCodexTUI

    app = ClawCodexTUI.__new__(ClawCodexTUI)
    phases = {"render": 1.0}
    app._emit_lifecycle_frame("x", 2.0, phases=phases)
    phases["render"] = 99.0  # mutate after emit
    assert received[0].phases == {"render": 1.0}
