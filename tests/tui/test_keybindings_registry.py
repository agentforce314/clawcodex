"""Tests for ``src.tui.keybindings_registry`` (WI-2.5)."""

from __future__ import annotations

import pytest

from src.tui.keybindings_registry import KeybindingRegistry, RegisteredBinding


# ------------------------------------------------------------------
# register / unregister
# ------------------------------------------------------------------


def test_register_returns_unregister_callable() -> None:
    registry = KeybindingRegistry()
    fired = []
    unreg = registry.register(
        RegisteredBinding(action="x", handler=lambda: fired.append(1))
    )
    assert callable(unreg)
    assert len(registry) == 1
    unreg()
    assert len(registry) == 0


def test_unregister_is_idempotent() -> None:
    registry = KeybindingRegistry()
    unreg = registry.register(
        RegisteredBinding(action="x", handler=lambda: None)
    )
    unreg()
    unreg()  # Second call is a no-op.
    assert len(registry) == 0


def test_unregister_specific_binding_only() -> None:
    """Two equal-shape registrations from different sources must remove
    the right one — using identity, not value equality."""

    registry = KeybindingRegistry()
    fired = []
    b1 = RegisteredBinding(action="x", handler=lambda: fired.append("first"))
    b2 = RegisteredBinding(action="x", handler=lambda: fired.append("second"))
    unreg1 = registry.register(b1)
    registry.register(b2)
    unreg1()
    assert len(registry) == 1
    # The remaining binding is b2.
    registry.fire("x")
    assert fired == ["second"]


# ------------------------------------------------------------------
# Global handler firing
# ------------------------------------------------------------------


def test_global_handler_fires() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(action="cancel", handler=lambda: fired.append("cancel"))
    )
    assert registry.fire("cancel") is True
    assert fired == ["cancel"]


def test_unmatched_action_returns_false() -> None:
    registry = KeybindingRegistry()
    registry.register(
        RegisteredBinding(action="cancel", handler=lambda: None)
    )
    assert registry.fire("nope") is False


# ------------------------------------------------------------------
# Context filtering
# ------------------------------------------------------------------


def test_context_handler_only_fires_when_context_active() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="scroll_top",
            handler=lambda: fired.append("scoped"),
            context_name="transcript.focused",
        )
    )
    # Inactive context: no handler available.
    assert registry.fire("scroll_top") is False
    assert fired == []

    registry.add_context("transcript.focused")
    assert registry.fire("scroll_top") is True
    assert fired == ["scoped"]


def test_add_context_then_remove_context_deactivates() -> None:
    registry = KeybindingRegistry()
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: None,
            context_name="transcript.focused",
        )
    )
    registry.add_context("transcript.focused")
    assert registry.fire("x") is True
    registry.remove_context("transcript.focused")
    assert registry.fire("x") is False


def test_remove_context_below_zero_is_silent_noop() -> None:
    registry = KeybindingRegistry()
    # Decrementing an inactive context must NOT raise — widget unmount
    # ordering can produce duplicate remove_context calls.
    registry.remove_context("never-active")
    assert registry.active_contexts() == set()


def test_active_contexts_snapshot() -> None:
    registry = KeybindingRegistry()
    registry.add_context("a")
    registry.add_context("b")
    assert registry.active_contexts() == {"a", "b"}


# ------------------------------------------------------------------
# Reference counting (NOT push/pop stack semantics)
# ------------------------------------------------------------------


def test_double_add_requires_double_remove() -> None:
    """The chapter's KeybindingContext.tsx uses a Set with refcounting;
    add_context/remove_context must NOT enforce LIFO discipline."""

    registry = KeybindingRegistry()
    registry.add_context("foo")
    registry.add_context("foo")
    # First removal: still active (count = 1).
    registry.remove_context("foo")
    assert "foo" in registry.active_contexts()
    # Second removal: now deactivates (count = 0).
    registry.remove_context("foo")
    assert "foo" not in registry.active_contexts()


def test_refcounting_preserves_active_state_under_overlapping_widgets() -> None:
    """Two widgets activate the same context; one unmounts; context still
    active for the second widget. (Real-world failure mode the refactoring-
    plan A14 / WI-2.5 spec calls out.)"""

    registry = KeybindingRegistry()
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: None,
            context_name="modal",
        )
    )
    # Widget A mounts and activates "modal".
    registry.add_context("modal")
    # Widget B mounts and also activates "modal" (overlapping modals).
    registry.add_context("modal")
    # Widget A unmounts.
    registry.remove_context("modal")
    # Widget B still mounted → context still active.
    assert registry.fire("x") is True


def test_add_context_rejects_empty_name() -> None:
    registry = KeybindingRegistry()
    with pytest.raises(ValueError):
        registry.add_context("")


# ------------------------------------------------------------------
# Most-specific-wins
# ------------------------------------------------------------------


def test_scoped_handler_beats_global_when_both_active() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="scroll_top",
            handler=lambda: fired.append("global"),
        )
    )
    registry.register(
        RegisteredBinding(
            action="scroll_top",
            handler=lambda: fired.append("scoped"),
            context_name="transcript.focused",
        )
    )
    registry.add_context("transcript.focused")
    registry.fire("scroll_top")
    assert fired == ["scoped"]


def test_global_fires_when_scope_inactive() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="scroll_top",
            handler=lambda: fired.append("global"),
        )
    )
    registry.register(
        RegisteredBinding(
            action="scroll_top",
            handler=lambda: fired.append("scoped"),
            context_name="transcript.focused",
        )
    )
    # No active context → only the global handler is reachable.
    registry.fire("scroll_top")
    assert fired == ["global"]


def test_later_registration_wins_at_same_specificity() -> None:
    """Two global handlers for the same action: deeper widget mounts last,
    so its registration appears later in the list, so it wins."""

    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(action="x", handler=lambda: fired.append("first"))
    )
    registry.register(
        RegisteredBinding(action="x", handler=lambda: fired.append("second"))
    )
    registry.fire("x")
    assert fired == ["second"]


def test_later_scoped_registration_wins_among_scoped_candidates() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: fired.append("first"),
            context_name="ctx",
        )
    )
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: fired.append("second"),
            context_name="ctx",
        )
    )
    registry.add_context("ctx")
    registry.fire("x")
    assert fired == ["second"]


# ------------------------------------------------------------------
# is_active dynamic predicate
# ------------------------------------------------------------------


def test_is_active_predicate_blocks_handler() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: fired.append("yes"),
            is_active=lambda: False,
        )
    )
    assert registry.fire("x") is False
    assert fired == []


def test_is_active_predicate_allows_handler() -> None:
    registry = KeybindingRegistry()
    fired = []
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: fired.append("yes"),
            is_active=lambda: True,
        )
    )
    assert registry.fire("x") is True
    assert fired == ["yes"]


def test_is_active_evaluated_at_fire_time() -> None:
    """The predicate is consulted on every fire(), not memoized at register()."""

    registry = KeybindingRegistry()
    fired = []
    state = {"on": True}
    registry.register(
        RegisteredBinding(
            action="x",
            handler=lambda: fired.append("hit"),
            is_active=lambda: state["on"],
        )
    )
    assert registry.fire("x") is True
    state["on"] = False
    assert registry.fire("x") is False
    state["on"] = True
    assert registry.fire("x") is True
    assert fired == ["hit", "hit"]


# ------------------------------------------------------------------
# Reentrancy — handler may register/unregister during fire()
# ------------------------------------------------------------------


def test_handler_can_unregister_itself_without_deadlock() -> None:
    registry = KeybindingRegistry()
    fired = []
    holder = {}

    def handler() -> None:
        fired.append("a")
        holder["unreg"]()

    holder["unreg"] = registry.register(
        RegisteredBinding(action="x", handler=handler)
    )
    registry.fire("x")
    assert fired == ["a"]
    assert len(registry) == 0


def test_handler_can_register_new_binding_without_deadlock() -> None:
    registry = KeybindingRegistry()
    fired = []

    def first() -> None:
        fired.append("first")
        registry.register(
            RegisteredBinding(action="y", handler=lambda: fired.append("second"))
        )

    registry.register(RegisteredBinding(action="x", handler=first))
    registry.fire("x")
    registry.fire("y")
    assert fired == ["first", "second"]


# ------------------------------------------------------------------
# bindings_for introspection
# ------------------------------------------------------------------


def test_bindings_for_returns_matching_subset() -> None:
    registry = KeybindingRegistry()
    b1 = RegisteredBinding(action="x", handler=lambda: None)
    b2 = RegisteredBinding(action="x", handler=lambda: None)
    b3 = RegisteredBinding(action="y", handler=lambda: None)
    registry.register(b1)
    registry.register(b2)
    registry.register(b3)
    matches = registry.bindings_for("x")
    assert len(matches) == 2
    assert all(m.action == "x" for m in matches)
