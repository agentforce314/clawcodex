"""Tests for ``src/state/app_state.py`` — Phase 2.1 AppState + onChange.

Verifies:
* The frozen dataclass shape supports the immutable-update discipline.
* ``replace_state`` produces a distinct reference (so the store doesn't
  identity-skip a real change).
* ``on_change_app_state`` mirrors model changes into bootstrap.
* The permission-mode listener fires exactly once per real change.
* The structural-coverage contract: every AppState field has a handler
  (real or sentinel) in ``_FIELD_HANDLERS``.
* End-to-end: store + onChange + listener form the chapter's chokepoint.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

import pytest

from src.bootstrap.state import (
    get_main_loop_model_override,
    reset_state_for_tests,
)
from src.state.app_state import (
    AppState,
    _FIELD_HANDLERS,
    create_app_state_store,
    get_default_app_state,
    on_change_app_state,
    replace_state,
    set_permission_mode_listener,
    set_session_metadata_listener,
)


@pytest.fixture(autouse=True)
def _reset_bootstrap_and_listeners():
    reset_state_for_tests()
    set_permission_mode_listener(None)
    set_session_metadata_listener(None)
    yield
    reset_state_for_tests()
    set_permission_mode_listener(None)
    set_session_metadata_listener(None)


class TestAppStateDataclass(unittest.TestCase):
    def test_default_state_is_well_formed(self) -> None:
        state = get_default_app_state()
        self.assertIsNone(state.main_loop_model)
        self.assertFalse(state.verbose)
        self.assertEqual(state.expanded_view, "none")
        self.assertEqual(state.permission_mode, "default")
        self.assertIsNone(state.initial_message)

    def test_state_is_frozen(self) -> None:
        state = get_default_app_state()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.verbose = True  # type: ignore[misc]

    def test_replace_state_returns_distinct_reference(self) -> None:
        state = get_default_app_state()
        new = replace_state(state, verbose=True)
        self.assertIsNot(new, state)
        self.assertTrue(new.verbose)
        self.assertFalse(state.verbose)  # original untouched

    def test_replace_state_preserves_unchanged_fields(self) -> None:
        state = get_default_app_state()
        state = replace_state(state, main_loop_model="claude-opus-4")
        new = replace_state(state, verbose=True)
        self.assertEqual(new.main_loop_model, "claude-opus-4")
        self.assertTrue(new.verbose)


class TestOnChangeMirrorsModel(unittest.TestCase):
    def test_main_loop_model_change_mirrors_to_bootstrap(self) -> None:
        old = get_default_app_state()
        new = replace_state(old, main_loop_model="claude-sonnet-4-6")

        on_change_app_state(old, new)

        self.assertEqual(get_main_loop_model_override(), "claude-sonnet-4-6")

    def test_clearing_main_loop_model_mirrors_to_bootstrap(self) -> None:
        old = replace_state(get_default_app_state(), main_loop_model="claude-opus-4")
        # Pre-condition: bootstrap is already set
        on_change_app_state(get_default_app_state(), old)
        self.assertEqual(get_main_loop_model_override(), "claude-opus-4")

        new = replace_state(old, main_loop_model=None)
        on_change_app_state(old, new)

        self.assertIsNone(get_main_loop_model_override())

    def test_no_change_does_not_touch_bootstrap(self) -> None:
        state = replace_state(get_default_app_state(), main_loop_model="claude-opus-4")
        on_change_app_state(state, state)
        # bootstrap was never touched because old == new
        self.assertIsNone(get_main_loop_model_override())


class TestPermissionModeNotification(unittest.TestCase):
    def test_permission_mode_change_fires_listener(self) -> None:
        received: list[str] = []
        set_permission_mode_listener(received.append)

        old = get_default_app_state()
        new = replace_state(old, permission_mode="plan")
        on_change_app_state(old, new)

        self.assertEqual(received, ["plan"])

    def test_permission_mode_change_fires_metadata_listener(self) -> None:
        received: list[dict[str, Any]] = []
        set_session_metadata_listener(received.append)

        old = get_default_app_state()
        new = replace_state(old, permission_mode="acceptEdits")
        on_change_app_state(old, new)

        self.assertEqual(received, [{"permission_mode": "acceptEdits"}])

    def test_listener_exception_does_not_propagate(self) -> None:
        """A buggy listener must not break the dispatch."""

        def raising_listener(_mode: str) -> None:
            raise RuntimeError("listener boom")

        set_permission_mode_listener(raising_listener)

        old = get_default_app_state()
        new = replace_state(old, permission_mode="plan")
        # Should not raise
        on_change_app_state(old, new)

    def test_no_change_does_not_fire(self) -> None:
        received: list[str] = []
        set_permission_mode_listener(received.append)

        old = replace_state(get_default_app_state(), permission_mode="plan")
        on_change_app_state(old, old)

        self.assertEqual(received, [])


class TestSideEffectCoverage(unittest.TestCase):
    """The architectural contract that the chapter's lesson demands:
    every AppState field has an entry in ``_FIELD_HANDLERS``.

    If a new AppState field lands without a handler (real or no-op),
    this test fails — the developer must explicitly decide whether the
    field needs a side effect, not implicitly skip the question."""

    def test_every_field_appears_in_handler_registry(self) -> None:
        field_names = {f.name for f in dataclasses.fields(AppState)}
        registry_keys = set(_FIELD_HANDLERS.keys())
        missing = field_names - registry_keys
        self.assertEqual(
            missing,
            set(),
            f"AppState fields without handlers in _FIELD_HANDLERS: {missing}. "
            f"Add a handler entry — every field in AppState must appear in _FIELD_HANDLERS.",
        )

    def test_no_handler_entries_for_unknown_fields(self) -> None:
        field_names = {f.name for f in dataclasses.fields(AppState)}
        registry_keys = set(_FIELD_HANDLERS.keys())
        extra = registry_keys - field_names
        self.assertEqual(
            extra,
            set(),
            f"_FIELD_HANDLERS has entries for non-existent fields: {extra}",
        )


class TestEndToEndStore(unittest.TestCase):
    """The chapter's chokepoint: a setState triggers onChange which
    mirrors to bootstrap. Verify the wiring end-to-end through
    ``create_app_state_store``."""

    def test_setstate_triggers_bootstrap_mirror(self) -> None:
        store = create_app_state_store()

        store.set_state(
            lambda prev: replace_state(prev, main_loop_model="claude-opus-4")
        )

        self.assertEqual(get_main_loop_model_override(), "claude-opus-4")

    def test_setstate_triggers_permission_mode_listener(self) -> None:
        received: list[str] = []
        set_permission_mode_listener(received.append)

        store = create_app_state_store()

        store.set_state(lambda prev: replace_state(prev, permission_mode="plan"))

        self.assertEqual(received, ["plan"])

    def test_subscribe_fires_after_onchange(self) -> None:
        """Order discipline: bootstrap mirror writes BEFORE subscribers
        re-render (the chapter's architectural property)."""
        order: list[str] = []

        # Subscribe a listener that captures bootstrap state at the time
        # of notification — should already reflect the new value.
        def listener() -> None:
            order.append(
                f"listener_sees:{get_main_loop_model_override()}",
            )

        store = create_app_state_store()
        store.subscribe(listener)

        store.set_state(
            lambda prev: replace_state(prev, main_loop_model="claude-opus-4")
        )

        # Listener sees the mirror — proves onChange ran first
        self.assertEqual(order, ["listener_sees:claude-opus-4"])


if __name__ == "__main__":
    unittest.main()
