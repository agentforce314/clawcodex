"""Tests for ``src.tui.keybindings_resolver`` (WI-2.3)."""

from __future__ import annotations

import pytest

from src.tui.keybindings_resolver import (
    KeybindingResolver,
    RESERVED_SHORTCUTS,
    ResolveStatus,
)
from src.tui.keybindings_schema import KeybindingEntry


# ------------------------------------------------------------------
# Single-key resolution
# ------------------------------------------------------------------


def test_single_key_resolves() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="cancel", keys=("ctrl+c",))]
    )
    result = resolver.resolve(["ctrl+c"])
    assert result.status is ResolveStatus.MATCHED
    assert result.action == "cancel"


def test_unknown_key_returns_no_match() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="cancel", keys=("ctrl+c",))]
    )
    result = resolver.resolve(["x"])
    assert result.status is ResolveStatus.NO_MATCH
    assert result.action is None


# ------------------------------------------------------------------
# Chord (multi-key) resolution
# ------------------------------------------------------------------


def test_chord_first_key_returns_pending() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="top", keys=("g", "g"))]
    )
    result = resolver.resolve(["g"])
    assert result.status is ResolveStatus.PENDING
    assert result.action is None


def test_chord_full_sequence_resolves() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="top", keys=("g", "g"))]
    )
    result = resolver.resolve(["g", "g"])
    assert result.status is ResolveStatus.MATCHED
    assert result.action == "top"


def test_chord_diverging_key_returns_no_match() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="top", keys=("g", "g"))]
    )
    # First key buffered; the second key 'z' cannot extend any chord
    # starting with 'g' so the resolver should return NO_MATCH.
    result = resolver.resolve(["g", "z"])
    assert result.status is ResolveStatus.NO_MATCH


# ------------------------------------------------------------------
# Longest-match precedence
# ------------------------------------------------------------------


def test_longest_match_wins_when_both_bound() -> None:
    """If both ``("g",)`` and ``("g", "g")`` are bound, the buffer ``["g"]``
    is PENDING (because ``("g", "g")`` is still reachable), but ``["g", "g"]``
    resolves to the chord — not the single-key binding."""

    resolver = KeybindingResolver(
        [
            KeybindingEntry(action="single_g", keys=("g",)),
            KeybindingEntry(action="double_g", keys=("g", "g")),
        ]
    )
    # Single key — chord still reachable, so PENDING (the resolver does
    # not commit early to the shorter binding).
    one = resolver.resolve(["g"])
    assert one.status is ResolveStatus.PENDING
    # Two keys — chord wins.
    two = resolver.resolve(["g", "g"])
    assert two.status is ResolveStatus.MATCHED
    assert two.action == "double_g"


def test_unique_single_key_resolves_immediately() -> None:
    """Without a chord competitor, a single key is MATCHED."""

    resolver = KeybindingResolver(
        [KeybindingEntry(action="single_g", keys=("g",))]
    )
    result = resolver.resolve(["g"])
    assert result.status is ResolveStatus.MATCHED


# ------------------------------------------------------------------
# Context filtering (when clause)
# ------------------------------------------------------------------


def test_global_binding_fires_with_empty_context() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="cancel", keys=("ctrl+c",))]
    )
    result = resolver.resolve(["ctrl+c"], context=set())
    assert result.status is ResolveStatus.MATCHED


def test_when_clause_blocks_when_context_inactive() -> None:
    resolver = KeybindingResolver(
        [
            KeybindingEntry(
                action="scroll_top",
                keys=("ctrl+home",),
                when="transcript.focused",
            )
        ]
    )
    # Context is empty → the binding is filtered out → NO_MATCH.
    result = resolver.resolve(["ctrl+home"], context=set())
    assert result.status is ResolveStatus.NO_MATCH


def test_when_clause_fires_when_context_active() -> None:
    resolver = KeybindingResolver(
        [
            KeybindingEntry(
                action="scroll_top",
                keys=("ctrl+home",),
                when="transcript.focused",
            )
        ]
    )
    result = resolver.resolve(
        ["ctrl+home"], context={"transcript.focused"}
    )
    assert result.status is ResolveStatus.MATCHED


def test_scoped_binding_beats_global_when_both_match() -> None:
    """Most-specific-context wins: a transcript-focused override beats a global one."""

    resolver = KeybindingResolver(
        [
            KeybindingEntry(action="global_top", keys=("ctrl+home",)),
            KeybindingEntry(
                action="scoped_top",
                keys=("ctrl+home",),
                when="transcript.focused",
            ),
        ]
    )
    # When the scope is active, the scoped binding wins.
    scoped_result = resolver.resolve(
        ["ctrl+home"], context={"transcript.focused"}
    )
    assert scoped_result.action == "scoped_top"

    # When the scope is inactive, only the global binding fires.
    global_result = resolver.resolve(["ctrl+home"], context=set())
    assert global_result.action == "global_top"


def test_context_filter_doesnt_break_pending_detection() -> None:
    """A scoped chord whose context is inactive should not contribute to
    the PENDING signal — otherwise a key would block waiting for a chord
    that can never fire."""

    resolver = KeybindingResolver(
        [
            KeybindingEntry(
                action="scoped_chord",
                keys=("g", "g"),
                when="transcript.focused",
            ),
        ]
    )
    # Context inactive: the chord is filtered out, so ['g'] alone is
    # NO_MATCH (no other binding is pending on 'g').
    result = resolver.resolve(["g"], context=set())
    assert result.status is ResolveStatus.NO_MATCH


# ------------------------------------------------------------------
# Empty / edge-case input
# ------------------------------------------------------------------


def test_empty_sequence_returns_no_match() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="cancel", keys=("ctrl+c",))]
    )
    result = resolver.resolve([])
    assert result.status is ResolveStatus.NO_MATCH


def test_no_bindings_always_no_match() -> None:
    resolver = KeybindingResolver([])
    assert resolver.resolve(["a"]).status is ResolveStatus.NO_MATCH
    assert resolver.resolve(["g", "g"]).status is ResolveStatus.NO_MATCH


# ------------------------------------------------------------------
# Reserved shortcuts
# ------------------------------------------------------------------


def test_reserved_shortcuts_includes_ctrl_c() -> None:
    assert "ctrl+c" in RESERVED_SHORTCUTS


def test_is_reserved_predicate() -> None:
    resolver = KeybindingResolver([])
    assert resolver.is_reserved("ctrl+c")
    assert not resolver.is_reserved("ctrl+a")


def test_find_reserved_collisions_flags_offending_bindings() -> None:
    resolver = KeybindingResolver(
        [
            KeybindingEntry(action="bad", keys=("ctrl+c",)),
            KeybindingEntry(action="ok", keys=("ctrl+a",)),
            # Chord starting with reserved key — also flagged.
            KeybindingEntry(action="bad_chord", keys=("ctrl+c", "x")),
        ]
    )
    collisions = resolver.find_reserved_collisions()
    actions = {c.action for c in collisions}
    assert actions == {"bad", "bad_chord"}


def test_find_reserved_collisions_clean_set_returns_empty() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="ok", keys=("ctrl+a",))]
    )
    assert resolver.find_reserved_collisions() == []


# ------------------------------------------------------------------
# replace_bindings (hot-reload)
# ------------------------------------------------------------------


def test_committed_mode_fires_shorter_binding_despite_reachable_chord() -> None:
    """Direct resolver-level test of the ``committed`` parameter contract.

    With both ``("g",)`` and ``("g","g")`` bound, ``resolve(["g"])``
    returns ``PENDING`` by default (longer chord still reachable) but
    ``resolve(["g"], committed=True)`` skips the prefix-extension check
    and returns ``MATCHED`` for ``single_g``. The ChordTracker timeout
    path uses this to fire the shorter binding post-timeout.
    """

    resolver = KeybindingResolver(
        [
            KeybindingEntry(action="single_g", keys=("g",)),
            KeybindingEntry(action="double_g", keys=("g", "g")),
        ]
    )
    pending = resolver.resolve(["g"])
    assert pending.status is ResolveStatus.PENDING

    committed = resolver.resolve(["g"], committed=True)
    assert committed.status is ResolveStatus.MATCHED
    assert committed.action == "single_g"


def test_committed_mode_returns_no_match_when_buffer_unresolvable() -> None:
    """Committed-mode does not invent matches: if no exact binding maps
    the buffer, ``NO_MATCH`` is the right answer regardless of pending
    extensions."""

    resolver = KeybindingResolver(
        [KeybindingEntry(action="double_g", keys=("g", "g"))]
    )
    # Default mode: ['g'] is PENDING (chord reachable).
    assert resolver.resolve(["g"]).status is ResolveStatus.PENDING
    # Committed mode: ['g'] alone is not bound, so NO_MATCH.
    assert (
        resolver.resolve(["g"], committed=True).status is ResolveStatus.NO_MATCH
    )


def test_replace_bindings_swaps_set() -> None:
    resolver = KeybindingResolver(
        [KeybindingEntry(action="old", keys=("a",))]
    )
    assert resolver.resolve(["a"]).action == "old"

    resolver.replace_bindings(
        [KeybindingEntry(action="new", keys=("a",))]
    )
    assert resolver.resolve(["a"]).action == "new"


def test_bindings_property_is_immutable_view() -> None:
    """Tuple is enough to keep callers from mutating in-place."""

    bindings = [KeybindingEntry(action="x", keys=("a",))]
    resolver = KeybindingResolver(bindings)
    assert isinstance(resolver.bindings, tuple)
    # Mutating the source list must not affect the resolver.
    bindings.append(KeybindingEntry(action="y", keys=("b",)))
    assert len(resolver.bindings) == 1
