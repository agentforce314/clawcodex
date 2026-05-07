"""Step 6 — once-aware listeners + cleanup on fire (G7).

The streaming executor creates one child abort controller per tool, and
each child registers a propagation listener on the parent. Without
``once=True`` semantics that listener accumulates: 50 tools per turn
times 10 turns = 500 dead listeners on the same parent signal. The
TS implementation uses ``addEventListener('abort', cb, {once: true})``
plus a WeakRef-based cleanup; the Python port now mirrors the
self-detaching ``once`` behavior.
"""
from __future__ import annotations

import unittest

from src.utils.abort_controller import (
    AbortController,
    AbortSignal,
    create_child_abort_controller,
)


class TestOnceListener(unittest.TestCase):
    def test_once_listener_fires_exactly_once(self) -> None:
        signal = AbortSignal()
        calls = []
        signal.add_listener(lambda: calls.append(1), once=True)
        signal._fire("first")
        # Re-firing isn't a thing in normal use, but the listener list
        # must be empty regardless after the first call.
        signal._fire("second")
        self.assertEqual(calls, [1])
        self.assertEqual(len(signal._listeners), 0)

    def test_default_listener_stays_registered(self) -> None:
        signal = AbortSignal()
        signal.add_listener(lambda: None)
        self.assertEqual(len(signal._listeners), 1)
        signal._fire("x")
        # Without once=True the listener stays — caller is responsible
        # for cleanup. (We just verify the default isn't surprise-once.)
        self.assertEqual(len(signal._listeners), 1)

    def test_add_listener_returns_registered_callable(self) -> None:
        signal = AbortSignal()

        def cb() -> None:
            return None

        registered = signal.add_listener(cb, once=True)
        # The wrapper, not the user callback. Caller can pass the
        # returned value to remove_listener for explicit cleanup.
        self.assertIsNot(registered, cb)
        signal.remove_listener(registered)
        self.assertEqual(len(signal._listeners), 0)


class TestChildControllerCleanup(unittest.TestCase):
    def test_parent_listener_detaches_when_child_aborts(self) -> None:
        parent = AbortController()
        baseline = len(parent.signal._listeners)
        child = create_child_abort_controller(parent)
        self.assertEqual(len(parent.signal._listeners), baseline + 1)
        child.abort("done")
        # The child detaches its propagation listener from the parent
        # so the parent doesn't accumulate dead handlers.
        self.assertEqual(len(parent.signal._listeners), baseline)

    def test_parent_listener_detaches_when_parent_aborts(self) -> None:
        parent = AbortController()
        child = create_child_abort_controller(parent)
        self.assertEqual(len(parent.signal._listeners), 1)
        parent.abort("kill all")
        # Once-fire semantics: the parent listener removes itself, so
        # the parent list is back to empty.
        self.assertEqual(len(parent.signal._listeners), 0)
        self.assertTrue(child.signal.aborted)

    def test_many_children_dont_leak_on_parent(self) -> None:
        """The historical bug: 1000 children, all abort, parent
        accumulates 1000 dead listeners."""
        parent = AbortController()
        children = [create_child_abort_controller(parent) for _ in range(100)]
        for c in children:
            c.abort("done")
        # Parent has zero parent-side listeners after all children exit.
        self.assertEqual(len(parent.signal._listeners), 0)


if __name__ == "__main__":
    unittest.main()
