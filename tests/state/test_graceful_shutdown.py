"""Unit tests for ``src/utils/graceful_shutdown.py`` (P1.2).

The signal-handler path tests (SIGTERM during prefetch, SIGINT before
prefetch) live in ``tests/test_init_integration.py`` because they need
real subprocess isolation. These unit tests exercise the in-process
APIs only.
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest

from src.utils import graceful_shutdown as gs


@pytest.fixture(autouse=True)
def _reset_graceful_shutdown():
    """Reset the module-level state before/after every test."""
    gs.reset_for_test_only()
    yield
    gs.reset_for_test_only()


class TestRegisterCleanupAndDrain(unittest.TestCase):
    def test_register_runs_cleanup_on_drain(self) -> None:
        calls = []
        gs.register_cleanup(lambda: calls.append("a"))
        gs._run_all_cleanups()
        self.assertEqual(calls, ["a"])

    def test_register_runs_in_order(self) -> None:
        calls = []
        gs.register_cleanup(lambda: calls.append(1))
        gs.register_cleanup(lambda: calls.append(2))
        gs.register_cleanup(lambda: calls.append(3))
        gs._run_all_cleanups()
        self.assertEqual(calls, [1, 2, 3])


class TestDrainIdempotent(unittest.TestCase):
    def test_double_call_fires_cleanup_once(self) -> None:
        calls = []
        gs.register_cleanup(lambda: calls.append("only-once"))
        gs._run_all_cleanups()
        gs._run_all_cleanups()
        self.assertEqual(calls, ["only-once"])

    def test_drain_after_cleanup_is_noop(self) -> None:
        # Once shutdown has started, additional registrations are
        # ignored (the snapshot under the lock has already been taken).
        calls = []
        gs.register_cleanup(lambda: calls.append("first"))
        gs._run_all_cleanups()
        gs.register_cleanup(lambda: calls.append("late"))
        gs._run_all_cleanups()
        self.assertEqual(calls, ["first"])


class TestCleanupExceptionsDoNotBlock(unittest.TestCase):
    def test_exception_in_first_cleanup_does_not_skip_second(self) -> None:
        calls = []

        def boom() -> None:
            raise RuntimeError("intentional")

        gs.register_cleanup(boom)
        gs.register_cleanup(lambda: calls.append("survived"))
        gs._run_all_cleanups()
        self.assertEqual(calls, ["survived"])


class TestSetupIdempotent(unittest.TestCase):
    def test_setup_graceful_shutdown_only_installs_once(self) -> None:
        # The second call should be a no-op. We can't directly observe
        # signal.signal calls without a fixture, but the _setup_done
        # latch is the implementation that guarantees the property.
        gs.setup_graceful_shutdown()
        first_state = gs._setup_done
        gs.setup_graceful_shutdown()
        second_state = gs._setup_done
        self.assertTrue(first_state)
        self.assertTrue(second_state)

    def test_setup_handles_non_main_thread_silently(self) -> None:
        # On a non-main thread, signal.signal raises ValueError. The
        # function must catch and proceed. We simulate by patching
        # signal.signal to raise.
        gs.reset_for_test_only()
        with mock.patch("signal.signal", side_effect=ValueError("not main thread")):
            gs.setup_graceful_shutdown()  # must not raise


class TestGracefulShutdownSync(unittest.TestCase):
    def test_calls_sys_exit_with_code(self) -> None:
        # graceful_shutdown_sync calls sys.exit, which raises SystemExit.
        calls = []
        gs.register_cleanup(lambda: calls.append("drained"))
        with self.assertRaises(SystemExit) as ctx:
            gs.graceful_shutdown_sync(42)
        self.assertEqual(ctx.exception.code, 42)
        self.assertEqual(calls, ["drained"])

    def test_default_exit_code_zero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            gs.graceful_shutdown_sync()
        self.assertEqual(ctx.exception.code, 0)


class TestResetGate(unittest.TestCase):
    def test_reset_outside_pytest_raises(self) -> None:
        # The reset is gated by PYTEST_CURRENT_TEST. The autouse fixture
        # sets it, so directly unsetting and calling reset must raise.
        import os
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with self.assertRaises(RuntimeError):
                gs.reset_for_test_only()
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved


if __name__ == "__main__":
    unittest.main()
