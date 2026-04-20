"""Unit tests for :class:`ChordTracker` and the default bindings."""

from __future__ import annotations

from src.tui.keybindings import (
    ChordBinding,
    ChordTracker,
    default_bindings,
    make_default_tracker,
)


def test_single_key_binding_matches_immediately():
    tracker = ChordTracker()
    tracker.add_binding(("G",), "transcript.bottom")
    assert tracker.on_key("G", now=1.0) == "transcript.bottom"


def test_two_key_chord_requires_both_keys():
    tracker = make_default_tracker()
    assert tracker.on_key("g", now=0.0) is None
    assert tracker.on_key("g", now=0.1) == "transcript.top"


def test_chord_timeout_resets_buffer():
    tracker = make_default_tracker()
    assert tracker.on_key("g", now=0.0) is None
    # Second "g" far outside the window should start a new chord, not
    # complete the old one.
    result = tracker.on_key("g", now=5.0)
    assert result is None  # buffer cleared, now pending "g" again


def test_non_matching_key_resets_buffer():
    tracker = make_default_tracker()
    assert tracker.on_key("g", now=0.0) is None
    # "z" cannot extend any chord that starts with "g" → reset.
    assert tracker.on_key("z", now=0.1) is None
    # Subsequent "g g" should still work cleanly.
    assert tracker.on_key("g", now=0.2) is None
    assert tracker.on_key("g", now=0.3) == "transcript.top"


def test_bracketed_motions():
    tracker = make_default_tracker()
    assert tracker.on_key("[", now=0.0) is None
    assert tracker.on_key("c", now=0.1) == "transcript.prev-change"

    assert tracker.on_key("]", now=0.2) is None
    assert tracker.on_key("m", now=0.3) == "transcript.next-message"


def test_default_bindings_are_stable_snapshot():
    bindings = default_bindings()
    assert isinstance(bindings, list)
    assert all(isinstance(b, ChordBinding) for b in bindings)
    actions = {b.action for b in bindings}
    # A minimum set the rest of the TUI may rely on:
    assert {"transcript.top", "transcript.bottom"}.issubset(actions)


def test_empty_chord_add_is_rejected():
    import pytest

    tracker = ChordTracker()
    with pytest.raises(ValueError):
        tracker.add_binding((), "noop")


def test_clear_removes_all_bindings():
    tracker = make_default_tracker()
    assert tracker.bindings
    tracker.clear()
    assert not tracker.bindings
    # With nothing registered, all keys are misses.
    assert tracker.on_key("g") is None
