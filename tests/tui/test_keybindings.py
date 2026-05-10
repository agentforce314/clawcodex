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


def test_timeout_fires_shorter_binding_when_chord_extension_unrealised():
    """Vim ``timeoutlen`` regression test (Phase-2 Critic blocker).

    With both ``("g",) → single_g`` and ``("g","g") → double_g`` bound,
    typing ``g`` then waiting past ``timeout_seconds`` MUST fire
    ``single_g``. Previously the resolver returned PENDING in the timeout-
    disambiguation path because ``("g","g")`` was still notionally
    reachable, so the shorter binding silently disappeared.
    """

    from src.tui.keybindings import ChordTracker

    tracker = ChordTracker(timeout_seconds=1.0)
    tracker.add_binding(("g",), "single_g")
    tracker.add_binding(("g", "g"), "double_g")

    # Buffer fills with first ``g`` — PENDING (shorter match deferred).
    assert tracker.on_key("g", now=0.0) is None
    # Far past the timeout: a fresh key arrives. Before discarding the
    # buffer, the tracker should commit the shorter binding.
    next_action = tracker.on_key("x", now=2.0)
    assert next_action == "single_g", (
        "shorter binding must fire post-timeout when the chord extension "
        "did not arrive"
    )


def test_timeout_with_only_chord_bound_drops_buffer_silently():
    """If only ``("g","g")`` is bound and the user types ``g`` then times
    out, no shorter binding to fire — buffer is silently dropped."""

    from src.tui.keybindings import ChordTracker

    tracker = ChordTracker(timeout_seconds=1.0)
    tracker.add_binding(("g", "g"), "double_g")
    assert tracker.on_key("g", now=0.0) is None
    # Past timeout; no shorter match exists.
    assert tracker.on_key("x", now=2.0) is None
