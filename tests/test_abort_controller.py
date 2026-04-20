import unittest

from src.utils.abort_controller import (
    AbortController,
    AbortError,
    AbortSignal,
    create_abort_controller,
    create_child_abort_controller,
)


class TestAbortSignal(unittest.TestCase):
    def test_initial_state(self):
        signal = AbortSignal()
        self.assertFalse(signal.aborted)
        self.assertIsNone(signal.reason)

    def test_fire_sets_aborted(self):
        signal = AbortSignal()
        signal._fire("test reason")
        self.assertTrue(signal.aborted)
        self.assertEqual(signal.reason, "test reason")

    def test_fire_invokes_listeners(self):
        signal = AbortSignal()
        called = []
        signal.add_listener(lambda: called.append(True))
        signal._fire("reason")
        self.assertEqual(len(called), 1)

    def test_remove_listener(self):
        signal = AbortSignal()
        called = []

        def cb():
            called.append(True)

        signal.add_listener(cb)
        signal.remove_listener(cb)
        signal._fire("reason")
        self.assertEqual(len(called), 0)

    def test_throw_if_aborted_noop_when_not_aborted(self):
        signal = AbortSignal()
        signal.throw_if_aborted()

    def test_throw_if_aborted_raises(self):
        signal = AbortSignal()
        signal._fire("test")
        with self.assertRaises(AbortError):
            signal.throw_if_aborted()


class TestAbortController(unittest.TestCase):
    def test_create(self):
        ctrl = create_abort_controller()
        self.assertFalse(ctrl.signal.aborted)

    def test_abort(self):
        ctrl = AbortController()
        ctrl.abort("user_interrupt")
        self.assertTrue(ctrl.signal.aborted)
        self.assertEqual(ctrl.signal.reason, "user_interrupt")

    def test_abort_idempotent(self):
        ctrl = AbortController()
        ctrl.abort("first")
        ctrl.abort("second")
        self.assertEqual(ctrl.signal.reason, "first")


class TestChildAbortController(unittest.TestCase):
    def test_parent_abort_propagates_to_child(self):
        parent = AbortController()
        child = create_child_abort_controller(parent)

        self.assertFalse(child.signal.aborted)
        parent.abort("parent_reason")
        self.assertTrue(child.signal.aborted)
        self.assertEqual(child.signal.reason, "parent_reason")

    def test_child_abort_does_not_propagate_to_parent(self):
        parent = AbortController()
        child = create_child_abort_controller(parent)

        child.abort("child_reason")
        self.assertTrue(child.signal.aborted)
        self.assertFalse(parent.signal.aborted)

    def test_already_aborted_parent(self):
        parent = AbortController()
        parent.abort("already_aborted")
        child = create_child_abort_controller(parent)
        self.assertTrue(child.signal.aborted)
        self.assertEqual(child.signal.reason, "already_aborted")

    def test_multiple_children(self):
        parent = AbortController()
        child1 = create_child_abort_controller(parent)
        child2 = create_child_abort_controller(parent)

        parent.abort("cascade")
        self.assertTrue(child1.signal.aborted)
        self.assertTrue(child2.signal.aborted)


if __name__ == "__main__":
    unittest.main()
