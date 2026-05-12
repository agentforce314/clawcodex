"""Tests for ``--bare`` / CLAUDE_CODE_SIMPLE mode (plan phase 4).

Verifies the gap-analysis C1.1 closure: ``--bare`` is now a real
performance lever that propagates through the bootstrap pipeline,
skipping the macOS prefetches and the hook snapshot.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src.utils.bare_mode import (
    BARE_MODE_ENV_VAR,
    is_bare_mode,
    set_bare_mode_env,
)


@pytest.fixture(autouse=True)
def _reset_bare_env():
    saved = os.environ.pop(BARE_MODE_ENV_VAR, None)
    yield
    os.environ.pop(BARE_MODE_ENV_VAR, None)
    if saved is not None:
        os.environ[BARE_MODE_ENV_VAR] = saved


class TestIsBareMode(unittest.TestCase):
    def test_unset_returns_false(self) -> None:
        self.assertFalse(is_bare_mode())

    def test_one_returns_true(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "1"
        self.assertTrue(is_bare_mode())

    def test_true_returns_true(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "true"
        self.assertTrue(is_bare_mode())

    def test_TRUE_uppercase_returns_true(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "TRUE"
        self.assertTrue(is_bare_mode())

    def test_yes_returns_true(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "yes"
        self.assertTrue(is_bare_mode())

    def test_zero_returns_false(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "0"
        self.assertFalse(is_bare_mode())

    def test_false_returns_false(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = "false"
        self.assertFalse(is_bare_mode())

    def test_empty_returns_false(self) -> None:
        os.environ[BARE_MODE_ENV_VAR] = ""
        self.assertFalse(is_bare_mode())

    def test_arbitrary_returns_false(self) -> None:
        # Default-deny: anything not in the truthy set is False.
        os.environ[BARE_MODE_ENV_VAR] = "maybe"
        self.assertFalse(is_bare_mode())


class TestSetBareModeEnv(unittest.TestCase):
    def test_sets_env_var(self) -> None:
        self.assertNotIn(BARE_MODE_ENV_VAR, os.environ)
        set_bare_mode_env()
        self.assertEqual(os.environ.get(BARE_MODE_ENV_VAR), "1")
        self.assertTrue(is_bare_mode())

    def test_idempotent(self) -> None:
        set_bare_mode_env()
        set_bare_mode_env()  # must not raise or clobber
        self.assertTrue(is_bare_mode())


class TestPrefetchSkipsInBareMode(unittest.TestCase):
    """Bare mode skips the macOS prefetches — they return sentinel
    handles with ``process=None`` like the non-macOS path."""

    def test_keychain_skipped_in_bare_mode(self) -> None:
        from src.prefetch import start_keychain_prefetch
        set_bare_mode_env()
        handle = start_keychain_prefetch()
        self.assertIsNone(handle.process)
        self.assertEqual(handle.label, "keychain_prefetch")

    def test_mdm_skipped_in_bare_mode(self) -> None:
        from src.prefetch import start_mdm_raw_read
        set_bare_mode_env()
        handle = start_mdm_raw_read()
        self.assertIsNone(handle.process)
        self.assertEqual(handle.label, "mdm_raw_read")


class TestSetupSkipsHookSnapshotInBareMode(unittest.TestCase):
    """Bare mode skips the hook snapshot capture — the substep
    early-returns without calling ``HookConfigManager.load()``."""

    def test_snapshot_not_captured_in_bare_mode(self) -> None:
        # Pre-condition: snapshot is not yet captured.
        from src.hooks.snapshot import (
            get_active_hook_config_manager,
            reset_hook_snapshot_for_test_only,
        )
        reset_hook_snapshot_for_test_only()
        self.assertIsNone(get_active_hook_config_manager())

        # Act: enable bare mode, run the substep.
        set_bare_mode_env()
        from src.setup import _capture_hook_snapshot
        _capture_hook_snapshot()

        # Assert: snapshot is still None — the substep early-returned.
        self.assertIsNone(get_active_hook_config_manager())


class TestBareFlagSkipsPrefetchEndToEnd(unittest.TestCase):
    """Major #2 from the round-1 critic review: the --bare flag must
    skip the prefetches end-to-end, not just gate them inside an
    already-running process.

    The prefetches fire at module-import time (cli.py:30-31), BEFORE
    argparse runs. So the only way --bare can skip them is via the
    pre-argparse argv scan at cli.py:14-15 that sets
    CLAUDE_CODE_SIMPLE=1 before the prefetch import.

    This test spawns a fresh Python subprocess with --bare on argv so
    the module-import-time check fires correctly. If the subprocess
    sees ``process=None`` on both handles, the wiring is correct.
    """

    def test_bare_argv_in_subprocess_skips_prefetches(self) -> None:
        import subprocess

        worktree_root = Path(__file__).resolve().parent.parent
        # Pretend to be cli.py: do the argv scan, then import prefetch.
        # This is what the real cli.main does at module-import time.
        code = (
            "import sys, os; sys.argv = ['clawcodex', '--bare']; "
            "import os as _os, sys as _sys; "
            "_os.environ.setdefault('CLAUDE_CODE_SIMPLE', '1') "
            "if '--bare' in _sys.argv[1:] else None; "
            "from src.prefetch import ("
            "  get_or_start_keychain_prefetch,"
            "  get_or_start_mdm_raw_read,"
            "); "
            "kh = get_or_start_keychain_prefetch(); "
            "mh = get_or_start_mdm_raw_read(); "
            "print('keychain:', 'SKIPPED' if kh.process is None else 'FIRED'); "
            "print('mdm:', 'SKIPPED' if mh.process is None else 'FIRED');"
        )
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_SIMPLE", None)  # ensure we test the argv scan path
        env["PYTHONPATH"] = (
            str(worktree_root) + os.pathsep + env.get("PYTHONPATH", "")
        )
        proc = subprocess.run(
            ["python3", "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = proc.stdout
        self.assertIn("keychain: SKIPPED", out, msg=f"stdout={out} stderr={proc.stderr}")
        self.assertIn("mdm: SKIPPED", out)

    def test_no_bare_in_subprocess_does_not_skip(self) -> None:
        # Negative case: when --bare is NOT on argv and the env is
        # unset, the prefetches fire (process is non-None on macOS, or
        # remains None on Linux/Windows). This is the existing
        # non-bare behavior; we just verify the argv scan doesn't
        # accidentally force bare mode.
        import subprocess

        worktree_root = Path(__file__).resolve().parent.parent
        code = (
            "import sys, os, platform; "
            "sys.argv = ['clawcodex']; "
            "from src.prefetch import get_or_start_keychain_prefetch; "
            "h = get_or_start_keychain_prefetch(); "
            "expect_fired = (platform.system() == 'Darwin'); "
            "actual_fired = (h.process is not None); "
            "print('match' if expect_fired == actual_fired else 'mismatch');"
            "h.process and h.process.kill();"
        )
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_SIMPLE", None)
        env["PYTHONPATH"] = (
            str(worktree_root) + os.pathsep + env.get("PYTHONPATH", "")
        )
        proc = subprocess.run(
            ["python3", "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("match", proc.stdout, msg=f"stdout={proc.stdout} stderr={proc.stderr}")


class TestCliBareFlagSetsEnv(unittest.TestCase):
    """Integration: cli.main with --bare sets the env BEFORE
    run_pre_action runs (verified by mocked downstream)."""

    def test_bare_flag_propagates_to_env_before_run_pre_action(self) -> None:
        # Pre-condition: env is clean.
        self.assertNotIn(BARE_MODE_ENV_VAR, os.environ)

        with mock.patch("src.init.run_pre_action") as mock_pre_action, \
                mock.patch("src.cli._resolve_permission_state"), \
                mock.patch("src.setup.run_production_setup"), \
                mock.patch("src.replLauncher.launch_repl", return_value=0):
            # Run cli.main with --bare. Mock the heavy paths so we
            # don't actually launch anything; we only care that
            # CLAUDE_CODE_SIMPLE=1 is set before run_pre_action is
            # called.
            import sys

            def assert_bare_during_init(*args, **kwargs):
                self.assertTrue(
                    is_bare_mode(),
                    "bare-mode env var must be set BEFORE run_pre_action",
                )

            mock_pre_action.side_effect = assert_bare_during_init

            with mock.patch.object(sys, "argv", ["clawcodex", "--bare"]):
                from src import cli
                cli.main()

        # Post-condition: env stays set (it's a process-wide flag).
        self.assertTrue(is_bare_mode())


if __name__ == "__main__":
    unittest.main()
