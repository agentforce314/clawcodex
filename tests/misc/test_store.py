"""Tests for ``src/utils/store.py``.

Mirrors the TS test discipline for ``state/store.ts``: updater-with-prev,
identity-skip on same reference, on_change-before-listeners ordering, and
the caller-contract lock that structural-equality-but-different-reference
DOES fire (proves we're using ``is``, not ``==``).
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from src.utils.store import Store, create_store


@dataclass
class _S:
    """Test fixture state — a small dataclass for identity testing."""
    x: int
    label: str = ""


class TestGetState(unittest.TestCase):
    def test_get_state_returns_initial(self) -> None:
        store = create_store(_S(x=1))
        self.assertEqual(store.get_state(), _S(x=1))

    def test_get_state_returns_same_reference(self) -> None:
        initial = _S(x=1)
        store = create_store(initial)
        self.assertIs(store.get_state(), initial)


class TestSetState(unittest.TestCase):
    def test_set_state_with_updater_replaces_state(self) -> None:
        store = create_store(_S(x=1))
        store.set_state(lambda prev: _S(x=2, label="bumped"))
        self.assertEqual(store.get_state(), _S(x=2, label="bumped"))

    def test_set_state_passes_prev_to_updater(self) -> None:
        store = create_store(_S(x=5))
        received: list[_S] = []
        store.set_state(lambda prev: (received.append(prev), prev)[1])
        self.assertEqual(received, [_S(x=5)])

    def test_set_state_identity_skip_returns_same_ref_does_not_notify(self) -> None:
        """Identity check: same reference returned → no notification."""
        store = create_store(_S(x=1))
        calls: list[int] = []
        store.subscribe(lambda: calls.append(1))

        # Return the exact same object reference
        store.set_state(lambda prev: prev)

        self.assertEqual(calls, [])

    def test_set_state_fresh_but_equal_object_DOES_notify(self) -> None:
        """Caller-contract lock: returning a freshly-constructed but
        structurally-identical dataclass fires onChange and listeners.

        This proves we're using ``is`` (identity), not ``==`` (equality).
        Without this test, a future refactor could "optimize" the store
        to use ``==`` and silently change behavior."""
        on_change_calls: list[tuple] = []
        store_with_oc = create_store(
            _S(x=1),
            on_change=lambda old, new: on_change_calls.append((old, new)),
        )
        listener_calls: list[int] = []
        store_with_oc.subscribe(lambda: listener_calls.append(1))

        # Construct a freshly-equal but distinct dataclass
        store_with_oc.set_state(lambda prev: _S(x=1))

        # Both onChange and listener fire because the reference changed
        self.assertEqual(len(on_change_calls), 1)
        self.assertEqual(listener_calls, [1])
        # And the equality check confirms they're structurally equal
        old, new = on_change_calls[0]
        self.assertEqual(old, new)
        self.assertIsNot(old, new)  # but distinct references


class TestSubscribe(unittest.TestCase):
    def test_subscribe_notifies_on_state_change(self) -> None:
        store = create_store(_S(x=1))
        calls: list[int] = []
        store.subscribe(lambda: calls.append(1))

        store.set_state(lambda prev: _S(x=2))

        self.assertEqual(calls, [1])

    def test_subscribe_does_not_notify_on_identity_skip(self) -> None:
        store = create_store(_S(x=1))
        calls: list[int] = []
        store.subscribe(lambda: calls.append(1))

        store.set_state(lambda prev: prev)

        self.assertEqual(calls, [])

    def test_unsubscribe_stops_notifications(self) -> None:
        store = create_store(_S(x=1))
        calls: list[int] = []
        unsubscribe = store.subscribe(lambda: calls.append(1))

        store.set_state(lambda prev: _S(x=2))
        unsubscribe()
        store.set_state(lambda prev: _S(x=3))

        self.assertEqual(calls, [1])

    def test_subscribe_multiple_listeners_all_fire_in_insertion_order(self) -> None:
        store = create_store(_S(x=1))
        order: list[str] = []

        store.subscribe(lambda: order.append("first"))
        store.subscribe(lambda: order.append("second"))
        store.subscribe(lambda: order.append("third"))

        store.set_state(lambda prev: _S(x=2))

        self.assertEqual(order, ["first", "second", "third"])


class TestOnChange(unittest.TestCase):
    def test_on_change_fires_before_listeners(self) -> None:
        """The core architectural property: side effects run before
        subscribers, so bootstrap-state mirrors and credential-cache
        clearing complete before the UI re-renders."""
        order: list[str] = []
        store = create_store(
            _S(x=1),
            on_change=lambda old, new: order.append("on_change"),
        )
        store.subscribe(lambda: order.append("listener"))

        store.set_state(lambda prev: _S(x=2))

        self.assertEqual(order, ["on_change", "listener"])

    def test_on_change_receives_old_and_new_state(self) -> None:
        received: list[tuple] = []
        store = create_store(
            _S(x=1),
            on_change=lambda old, new: received.append((old, new)),
        )

        store.set_state(lambda prev: _S(x=2))

        self.assertEqual(len(received), 1)
        old, new = received[0]
        self.assertEqual(old, _S(x=1))
        self.assertEqual(new, _S(x=2))

    def test_on_change_sees_committed_state(self) -> None:
        """When onChange fires, ``store.get_state()`` already returns the
        new state — onChange sees committed state, not in-flight."""
        seen_via_getter: list[_S] = []

        def on_change(old: _S, new: _S) -> None:
            seen_via_getter.append(store.get_state())

        store = create_store(_S(x=1), on_change=on_change)
        store.set_state(lambda prev: _S(x=42))

        self.assertEqual(seen_via_getter, [_S(x=42)])

    def test_on_change_not_called_when_no_state_change(self) -> None:
        calls: list[int] = []
        store = create_store(_S(x=1), on_change=lambda old, new: calls.append(1))

        store.set_state(lambda prev: prev)

        self.assertEqual(calls, [])


class TestReentry(unittest.TestCase):
    def test_listener_can_call_set_state_no_infinite_recursion(self) -> None:
        """A listener that triggers another set_state must not deadlock or
        recurse unbounded. The second set_state runs as a normal sequential
        call; its listeners fire once for it."""
        store = create_store(_S(x=0))
        order: list[int] = []
        recursion_guard: list[int] = [0]

        def listener() -> None:
            order.append(store.get_state().x)
            # Trigger one more set_state but only the first time
            if recursion_guard[0] == 0:
                recursion_guard[0] = 1
                store.set_state(lambda prev: _S(x=prev.x + 1))

        store.subscribe(listener)
        store.set_state(lambda prev: _S(x=1))

        # First mutation set x=1 → listener fires (sees 1) → triggers x=2
        # → listener fires again (sees 2). Two total notifications.
        self.assertEqual(order, [1, 2])


class TestUpdaterErrors(unittest.TestCase):
    def test_set_state_updater_raises_state_unchanged(self) -> None:
        """If the updater raises, state must remain at prev and no
        notifications fire."""
        on_change_calls: list[int] = []
        store = create_store(
            _S(x=1),
            on_change=lambda old, new: on_change_calls.append(1),
        )
        calls: list[int] = []
        store.subscribe(lambda: calls.append(1))

        def raising_updater(prev: _S) -> _S:
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            store.set_state(raising_updater)

        self.assertEqual(store.get_state(), _S(x=1))  # unchanged
        self.assertEqual(calls, [])
        self.assertEqual(on_change_calls, [])

    def test_on_change_raises_state_already_committed(self) -> None:
        """If onChange raises, the state has already been committed at
        that point (lock the documented contract from store.py:73-74).
        Subscribers also fire only if the listener-loop reaches them — onChange
        raising blocks the loop, so subscribers should NOT fire."""
        def bad_on_change(old: _S, new: _S) -> None:
            raise RuntimeError("on_change_boom")

        store = create_store(_S(x=1), on_change=bad_on_change)
        listener_calls: list[int] = []
        store.subscribe(lambda: listener_calls.append(1))

        with self.assertRaises(RuntimeError):
            store.set_state(lambda prev: _S(x=2))

        # State committed BEFORE on_change raised
        self.assertEqual(store.get_state(), _S(x=2))
        # Listener loop never reached
        self.assertEqual(listener_calls, [])


class TestStoreType(unittest.TestCase):
    def test_create_store_returns_a_store(self) -> None:
        store = create_store(_S(x=1))
        self.assertIsInstance(store, Store)


if __name__ == "__main__":
    unittest.main()
