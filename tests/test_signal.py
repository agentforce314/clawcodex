"""Tests for ``src/utils/signal.py``.

Mirrors the TS test discipline for ``utils/signal.ts``: subscribe-then-emit,
unsubscribe, clear, plus the listener-mutates-subscribers and
listener-raises edge cases. Lockfile for the dict-as-ordered-set
insertion-order behavior.
"""

from __future__ import annotations

import unittest

from src.utils.signal import Signal, create_signal


class TestSubscribeThenEmit(unittest.TestCase):
    def test_subscribe_then_emit_calls_listener(self) -> None:
        sig = create_signal()
        calls: list[tuple] = []

        sig.subscribe(lambda *args: calls.append(args))
        sig.emit("hello")

        self.assertEqual(calls, [("hello",)])

    def test_subscribe_returns_an_unsubscribe_function(self) -> None:
        sig = create_signal()
        unsubscribe = sig.subscribe(lambda: None)
        self.assertTrue(callable(unsubscribe))

    def test_emit_with_no_listeners_is_a_noop(self) -> None:
        sig = create_signal()
        sig.emit("ignored")  # should not raise


class TestInsertionOrder(unittest.TestCase):
    def test_emit_calls_listeners_in_insertion_order(self) -> None:
        """The dict-as-ordered-set lockfile. CPython 3.7+ guarantees this."""
        sig = create_signal()
        order: list[str] = []

        sig.subscribe(lambda: order.append("first"))
        sig.subscribe(lambda: order.append("second"))
        sig.subscribe(lambda: order.append("third"))

        sig.emit()

        self.assertEqual(order, ["first", "second", "third"])


class TestUnsubscribe(unittest.TestCase):
    def test_unsubscribe_removes_listener(self) -> None:
        sig = create_signal()
        calls: list[int] = []

        unsubscribe = sig.subscribe(lambda: calls.append(1))
        sig.emit()
        unsubscribe()
        sig.emit()

        self.assertEqual(calls, [1])  # listener fired once before unsubscribe

    def test_unsubscribe_is_idempotent(self) -> None:
        sig = create_signal()
        unsubscribe = sig.subscribe(lambda: None)
        unsubscribe()
        unsubscribe()  # second call: should not raise


class TestClear(unittest.TestCase):
    def test_clear_removes_all_listeners(self) -> None:
        sig = create_signal()
        calls: list[int] = []

        sig.subscribe(lambda: calls.append(1))
        sig.subscribe(lambda: calls.append(2))
        sig.clear()
        sig.emit()

        self.assertEqual(calls, [])


class TestSubscriberMutationDuringEmit(unittest.TestCase):
    def test_listener_that_subscribes_another_listener_is_safe(self) -> None:
        """Subscribing a fresh listener inside an emit callback must not
        corrupt the iteration. The new listener does NOT receive the
        in-flight emit; it fires on subsequent emits."""
        sig = create_signal()
        calls: list[str] = []

        def first_listener() -> None:
            calls.append("first")
            sig.subscribe(lambda: calls.append("late"))

        sig.subscribe(first_listener)
        sig.emit()

        self.assertEqual(calls, ["first"])  # late listener NOT called this emit

        sig.emit()
        self.assertIn("late", calls)  # late listener fires on the next emit

    def test_listener_that_unsubscribes_itself_is_safe(self) -> None:
        """A listener that removes itself during the callback must complete
        the current emit without raising, and must not fire on subsequent
        emits."""
        sig = create_signal()
        calls: list[int] = []
        unsubscribe_holder: list = []

        def self_removing() -> None:
            calls.append(1)
            unsubscribe_holder[0]()

        unsubscribe = sig.subscribe(self_removing)
        unsubscribe_holder.append(unsubscribe)

        sig.emit()
        sig.emit()

        self.assertEqual(calls, [1])  # only the first emit triggers


class TestExceptionPropagation(unittest.TestCase):
    def test_emit_with_listener_that_raises_propagates(self) -> None:
        """Matches TS behavior: ``signal.ts`` does not catch listener
        exceptions. They propagate to the caller of emit."""
        sig = create_signal()

        def raiser() -> None:
            raise RuntimeError("boom")

        sig.subscribe(raiser)

        with self.assertRaises(RuntimeError):
            sig.emit()


class TestEmitArgs(unittest.TestCase):
    def test_emit_with_positional_and_kwargs(self) -> None:
        sig = create_signal()
        received: list[tuple] = []

        sig.subscribe(lambda *args, **kwargs: received.append((args, kwargs)))
        sig.emit("a", "b", x=1, y=2)

        self.assertEqual(received, [(("a", "b"), {"x": 1, "y": 2})])


class TestType(unittest.TestCase):
    def test_create_signal_returns_a_signal(self) -> None:
        sig = create_signal()
        self.assertIsInstance(sig, Signal)

    def test_each_create_signal_call_returns_a_fresh_instance(self) -> None:
        a = create_signal()
        b = create_signal()
        self.assertIsNot(a, b)
        a.subscribe(lambda: None)
        self.assertEqual(len(a._listeners), 1)
        self.assertEqual(len(b._listeners), 0)


if __name__ == "__main__":
    unittest.main()
