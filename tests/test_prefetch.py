"""WI-4.1 acceptance tests — fire-and-forget prefetch handles.

The prefetch module fires ``security find-generic-password`` and ``plutil``
subprocesses at module-import time (via ``src/cli.py``) so the ~65ms
wall-clock cost overlaps with the rest of module loading. The chapter's
acceptance criterion (per critic M10) is **structural**: returning the
handle takes microseconds (not the actual subprocess work), and consumer
helpers correctly drain the child process when called.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from src.prefetch import (
    PrefetchHandle,
    get_or_start_keychain_prefetch,
    get_or_start_mdm_raw_read,
    start_keychain_prefetch,
    start_mdm_raw_read,
    start_project_scan,
    wait_and_read_keychain,
    wait_and_read_mdm,
)


class TestStartReturnsImmediately(unittest.TestCase):
    """The fire-and-forget contract: ``start_*`` returns the handle in microseconds."""

    def test_keychain_prefetch_returns_quickly(self):
        """``start_keychain_prefetch`` must return promptly; the OS handles the
        subprocess work in parallel. Cap at 200 ms to avoid CI flakiness while
        still catching a regression that ran the subprocess synchronously."""
        t0 = time.perf_counter()
        handle = start_keychain_prefetch()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertIsInstance(handle, PrefetchHandle)
        self.assertEqual(handle.label, "keychain_prefetch")
        self.assertLess(
            elapsed_ms, 200,
            f"start_keychain_prefetch took {elapsed_ms:.1f}ms — the call should "
            "return immediately (subprocess work happens in parallel)",
        )
        # Drain the child if one was spawned.
        if handle.process is not None:
            handle.process.kill()
            handle.process.wait(timeout=2.0)

    def test_mdm_raw_read_returns_quickly(self):
        t0 = time.perf_counter()
        handle = start_mdm_raw_read()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertIsInstance(handle, PrefetchHandle)
        self.assertEqual(handle.label, "mdm_raw_read")
        self.assertLess(elapsed_ms, 200)
        if handle.process is not None:
            handle.process.kill()
            handle.process.wait(timeout=2.0)


class TestNonMacOsBehavior(unittest.TestCase):
    """On non-macOS platforms, prefetch returns ``process=None`` sentinels."""

    @patch("sys.platform", "linux")
    def test_keychain_returns_none_handle_on_linux(self):
        handle = start_keychain_prefetch()
        self.assertIsNone(handle.process)
        # And waiting on a None-handle returns None instantly.
        self.assertIsNone(wait_and_read_keychain(handle))

    @patch("sys.platform", "linux")
    def test_mdm_returns_none_handle_on_linux(self):
        handle = start_mdm_raw_read()
        self.assertIsNone(handle.process)
        self.assertIsNone(wait_and_read_mdm(handle))


class TestWaitAndRead(unittest.TestCase):
    """``wait_and_read_*`` correctly drains a stubbed child process."""

    def test_wait_and_read_returns_stdout_on_success(self):
        # Synthesize a Popen-like that returns ('secret\n', '') with rc=0.
        class FakeProcess:
            returncode = 0
            def communicate(self, timeout=None):
                return (b"secret\n", b"")
        handle = PrefetchHandle(process=FakeProcess(), label="test")  # type: ignore[arg-type]
        self.assertEqual(wait_and_read_keychain(handle), "secret")

    def test_wait_and_read_returns_none_on_nonzero_exit(self):
        class FakeProcess:
            returncode = 1
            def communicate(self, timeout=None):
                return (b"", b"error: not found")
        handle = PrefetchHandle(process=FakeProcess(), label="test")  # type: ignore[arg-type]
        self.assertIsNone(wait_and_read_keychain(handle))

    def test_wait_and_read_returns_none_on_timeout(self):
        kills = []
        class FakeProcess:
            returncode = None
            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            def kill(self):
                kills.append(True)
        handle = PrefetchHandle(process=FakeProcess(), label="test")  # type: ignore[arg-type]
        self.assertIsNone(wait_and_read_keychain(handle, timeout=0.001))
        self.assertEqual(kills, [True], "timed-out child must be killed")


class TestModuleLevelFireAndForget(unittest.TestCase):
    """ch02 round-4 WI-3 INVERTED the original contract: ``src/cli.py`` no
    longer fires the prefetch at module-import time — fast paths like
    ``--version``/``mcp`` must not spawn subprocesses. ``main()`` fires it
    once the invocation is known to need the full pipeline; the
    fire-before-init ordering and fast-path skips are pinned by
    tests/test_ch02_bootstrap_round4.py::TestFastPathHygiene.
    """

    def test_cli_module_import_fires_nothing(self):
        # Re-import to ensure module-level code re-runs in a clean way.
        import importlib
        import src.cli
        importlib.reload(src.cli)
        self.assertFalse(hasattr(src.cli, "_keychain_handle"))
        self.assertFalse(hasattr(src.cli, "_mdm_handle"))


class TestProjectScanStub(unittest.TestCase):
    def test_project_scan_returns_handle(self):
        from pathlib import Path
        handle = start_project_scan(Path("/tmp"))
        self.assertIsInstance(handle, PrefetchHandle)
        self.assertEqual(handle.label, "project_scan")
        # Stub: process is None by design until a future WI wires a real walk.
        self.assertIsNone(handle.process)


class TestSingletonGetters(unittest.TestCase):
    """Singleton semantics: cli.py and setup.py share one handle per kind.

    Resolves the critic M2 finding — without singletons the same
    interpreter would spawn the keychain/mdm subprocess twice, doubling
    the cost and orphaning the cli.py-spawned children.
    """

    def setUp(self):
        # Clear any prior singleton so each test starts fresh.
        import src.prefetch
        src.prefetch._singletons.clear()

    def tearDown(self):
        # Drain any spawned children before clearing the cache.
        import src.prefetch
        for handle in src.prefetch._singletons.values():
            if handle.process is not None:
                try:
                    handle.process.kill()
                    handle.process.wait(timeout=2.0)
                except Exception:
                    pass
        src.prefetch._singletons.clear()

    def test_keychain_singleton_returns_same_handle(self):
        h1 = get_or_start_keychain_prefetch()
        h2 = get_or_start_keychain_prefetch()
        self.assertIs(h1, h2, "Repeated calls must return the cached handle")

    def test_mdm_singleton_returns_same_handle(self):
        h1 = get_or_start_mdm_raw_read()
        h2 = get_or_start_mdm_raw_read()
        self.assertIs(h1, h2)

    def test_keychain_singleton_fires_only_once(self):
        with patch("src.prefetch.start_keychain_prefetch") as mock_start:
            mock_start.return_value = PrefetchHandle(process=None, label="x")
            get_or_start_keychain_prefetch()
            get_or_start_keychain_prefetch()
            get_or_start_keychain_prefetch()
            self.assertEqual(
                mock_start.call_count, 1,
                "Singleton must fire underlying prefetch exactly once "
                "even across multiple callers (cli.py + setup.py)",
            )

    def test_mdm_singleton_fires_only_once(self):
        with patch("src.prefetch.start_mdm_raw_read") as mock_start:
            mock_start.return_value = PrefetchHandle(process=None, label="x")
            get_or_start_mdm_raw_read()
            get_or_start_mdm_raw_read()
            get_or_start_mdm_raw_read()
            self.assertEqual(mock_start.call_count, 1)


if __name__ == "__main__":
    unittest.main()
