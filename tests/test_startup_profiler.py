"""Tests for ``src/utils/startup_profiler.py`` — gap #6 (WI-0.1).

The profiler is normally a process-level singleton with state captured at
import. Tests reload the module per-case via ``importlib.reload`` so each
test exercises a clean env-gate and a fresh phase log without leaking
into other tests.

Critic note (M10): the no-op-when-disabled assertion is structural
(``get_internal_phase_log() == []``), not timing-based — Python function
overhead jitters above the chapter's wall-clock thresholds on shared CI.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def _fresh_profiler(env: dict[str, str] | None = None):
    """Reload the profiler module under a clean environment.

    Patches ``os.environ`` BEFORE the module reloads so the env-gate is
    re-read against the patched dict. Returns the freshly-loaded module.
    """
    real_env = env if env is not None else {}
    # Drop any cached copy so the import-time gate evaluation runs again.
    sys.modules.pop("src.utils.startup_profiler", None)
    with patch.dict("os.environ", real_env, clear=True):
        return importlib.import_module("src.utils.startup_profiler")


class TestStartupProfiler(unittest.TestCase):
    """WI-0.1 acceptance tests."""

    def tearDown(self):
        """Disarm the module-level atexit handler for the next test/process.

        The profiler registers ``_flush_on_exit`` at import time so it can
        emit a report when the user's process exits. In tests, the LAST
        test's state is what atexit sees when pytest exits — without this
        cleanup, a test that left ``_PROFILING_ENABLED=True`` plus some
        checkpoints would cause a real ``~/.claude/startup-perf/`` write
        on every test-suite run. Resetting the module state in tearDown
        keeps the test side-effect-free for the user's filesystem.
        """
        mod = sys.modules.get("src.utils.startup_profiler")
        if mod is not None:
            mod._PROFILING_ENABLED = False  # type: ignore[attr-defined]
            mod.reset_profiler_for_test_only()

    def test_records_two_entries_with_monotonic_timestamps(self):
        mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": "1"})
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("a")
        mod.profile_checkpoint("b")
        log = mod.get_internal_phase_log()
        self.assertEqual([name for name, _ in log], ["a", "b"])
        self.assertLessEqual(log[0][1], log[1][1])

    def test_report_returns_markdown_with_phase_deltas(self):
        mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": "1"})
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("first")
        mod.profile_checkpoint("second")
        report = mod.profile_report()
        self.assertIn("# Startup Profile", report)
        self.assertIn("first", report)
        self.assertIn("second", report)
        # Delta column is present.
        self.assertIn("Delta", report)

    def test_is_profiling_enabled_reads_env_truthy_values(self):
        for truthy in ("1", "true", "TRUE", "yes", "Yes"):
            with self.subTest(value=truthy):
                mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": truthy})
                self.assertTrue(
                    mod.is_profiling_enabled(),
                    f"{truthy!r} should be truthy",
                )

    def test_is_profiling_enabled_reads_env_falsy_values(self):
        for falsy in ("0", "false", "no", "", "garbage"):
            with self.subTest(value=falsy):
                mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": falsy})
                self.assertFalse(
                    mod.is_profiling_enabled(),
                    f"{falsy!r} should be falsy",
                )

    def test_is_profiling_enabled_unset_env_is_falsy(self):
        # No CLAUDE_CODE_PROFILE_STARTUP key in env at all.
        mod = _fresh_profiler({})
        self.assertFalse(mod.is_profiling_enabled())

    def test_no_op_when_disabled_structural_assertion(self):
        """Critic-resolved M10: zero-tolerance structural test, not timing.

        When the env-gate is false, profile_checkpoint MUST NOT record any
        entries — verified by inspecting the internal log directly. Avoids
        the flake-prone ``<1µs per call`` wall-clock assertion the original
        plan attempted.
        """
        mod = _fresh_profiler({})  # env unset, gate is false
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("ignored_a")
        mod.profile_checkpoint("ignored_b")
        mod.profile_checkpoint("ignored_c")
        self.assertEqual(mod.get_internal_phase_log(), [])

    def test_atexit_handler_writes_to_disk_when_enabled(self):
        mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": "1"})
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("setup")
        mod.profile_checkpoint("done")

        # Patch the output directory to a tmp path so we don't pollute
        # the real ~/.claude. tmp_path-style isolation via tmpdir.
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir) / "startup-perf"
            with patch.object(mod, "_OUTPUT_DIR", tmp_root):
                mod._flush_on_exit()  # type: ignore[attr-defined]
            files = list(tmp_root.glob("*.txt"))
            self.assertEqual(len(files), 1, f"expected one report file, got {files!r}")
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("setup", content)
            self.assertIn("done", content)

    def test_atexit_handler_no_write_when_disabled(self):
        mod = _fresh_profiler({})  # gate false
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("ignored")

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir) / "startup-perf"
            with patch.object(mod, "_OUTPUT_DIR", tmp_root):
                mod._flush_on_exit()  # type: ignore[attr-defined]
            # Directory should not have been created at all.
            self.assertFalse(
                tmp_root.exists(),
                "Disabled profiler must not touch the output directory",
            )

    def test_report_with_no_checkpoints_is_empty_skeleton(self):
        mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": "1"})
        mod.reset_profiler_for_test_only()
        report = mod.profile_report()
        self.assertIn("no checkpoints recorded", report)

    def test_atexit_handler_swallows_io_errors(self):
        """Critic m1: atexit must NEVER raise — process exit must complete.

        Patches ``Path.mkdir`` on the output directory to raise PermissionError
        (a real-world failure mode when ``~/.claude`` is on a read-only mount
        or owned by root). The handler must swallow the exception cleanly.
        """
        mod = _fresh_profiler({"CLAUDE_CODE_PROFILE_STARTUP": "1"})
        mod.reset_profiler_for_test_only()
        mod.profile_checkpoint("setup")

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir) / "startup-perf"
            with patch.object(mod, "_OUTPUT_DIR", tmp_root):
                with patch.object(
                    type(tmp_root), "mkdir", side_effect=PermissionError("read-only")
                ):
                    # Must not raise — atexit must always complete.
                    try:
                        mod._flush_on_exit()  # type: ignore[attr-defined]
                    except Exception as exc:
                        self.fail(f"_flush_on_exit must swallow exceptions, got {exc!r}")
            # No file written because mkdir failed.
            self.assertFalse(tmp_root.exists())

    def test_output_dir_honors_claude_config_dir_env(self):
        """Critic m6: ``CLAWCODEX_CONFIG_DIR`` should redirect the report path."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mod = _fresh_profiler({
                "CLAUDE_CODE_PROFILE_STARTUP": "1",
                "CLAWCODEX_CONFIG_DIR": tmpdir,
            })
            self.assertEqual(
                mod._OUTPUT_DIR,  # type: ignore[attr-defined]
                Path(tmpdir) / "startup-perf",
            )


if __name__ == "__main__":
    unittest.main()
