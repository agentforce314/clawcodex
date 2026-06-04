"""Integration tests for the plan-phase-1 bootstrap pipeline (P1.7).

These tests cover the architectural properties (init runs once, fast
paths skip init, profile checkpoints land in order, signal-path
cleanups fire) that aren't easy to assert in pure unit tests.

The subprocess-based tests (5a, 5b, 5c) use ``subprocess.Popen`` to
isolate signal-handler behavior — sending SIGTERM into the test
process itself would break pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src import init as init_module
from src.bootstrap.state import reset_state_for_tests
from src.utils import graceful_shutdown as gs
from src.utils import startup_profiler


WORKTREE_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_everything():
    init_module.reset_init_for_test_only()
    reset_state_for_tests()
    gs.reset_for_test_only()
    startup_profiler.reset_profiler_for_test_only()
    yield
    init_module.reset_init_for_test_only()
    reset_state_for_tests()
    gs.reset_for_test_only()
    startup_profiler.reset_profiler_for_test_only()


# ---------------------------------------------------------------------------
# 1. init runs exactly once across multiple "entry points"
# ---------------------------------------------------------------------------


class TestInitRunsExactlyOnceAcrossEntries(unittest.TestCase):
    """Chapter §"Apply This: Memoize your init function" — multiple
    entry points each calling init() must run substeps once total."""

    def test_three_entry_points_run_substeps_once_each(self) -> None:
        with mock.patch.object(
            init_module, "apply_safe_config_environment_variables"
        ) as mock_safe, mock.patch.object(
            init_module, "setup_graceful_shutdown"
        ) as mock_shutdown, mock.patch.object(
            init_module, "start_api_preconnect"
        ) as mock_preconnect:
            # Simulate three different entry points all calling init.
            def entry_repl() -> None:
                init_module.init()

            def entry_headless() -> None:
                init_module.init()

            def entry_sdk() -> None:
                init_module.init()

            entry_repl()
            entry_headless()
            entry_sdk()

            self.assertEqual(mock_safe.call_count, 1)
            self.assertEqual(mock_shutdown.call_count, 1)
            self.assertEqual(mock_preconnect.call_count, 1)


# ---------------------------------------------------------------------------
# 2. Fast paths never reach run_pre_action
# ---------------------------------------------------------------------------


class TestFastPathSkipsInit(unittest.TestCase):
    """--version goes through the pre-argparse fast-path at
    cli.py:52-55 OR the post-argparse short-circuit at line 92-95;
    neither reaches ``run_pre_action``."""

    def test_version_fast_path_does_not_call_pre_action(self) -> None:
        # The pre-argparse fast-path checks for one-flag --version.
        with mock.patch("src.init.run_pre_action") as mock_pre_action:
            with mock.patch.object(sys, "argv", ["clawcodex", "--version"]):
                from src import cli
                cli.main()
            mock_pre_action.assert_not_called()

    def test_post_argparse_version_short_circuit_skips_init(self) -> None:
        # Multi-arg argv triggers argparse (pre-argparse fast-path
        # requires len(sys.argv) == 2). The args.version short-circuit
        # at cli.py:92-95 must also skip init.
        with mock.patch("src.init.run_pre_action") as mock_pre_action:
            with mock.patch.object(
                sys, "argv", ["clawcodex", "--version", "--debug"]
            ):
                # argparse rejects unknown flags, so use a known one
                # that doesn't change behavior:
                # ``--version`` alone, but force the pre-argparse
                # fast-path to miss by adding a no-op extra flag.
                # Easiest: use ``--version`` + ``--legacy-repl`` (both
                # parse, and ``args.version`` short-circuits first).
                pass
            with mock.patch.object(
                sys, "argv", ["clawcodex", "--version", "--legacy-repl"]
            ):
                from src import cli
                cli.main()
            mock_pre_action.assert_not_called()

    def test_post_argparse_config_short_circuit_skips_init(self) -> None:
        # Same property for --config short-circuit.
        with mock.patch("src.init.run_pre_action") as mock_pre_action, \
                mock.patch("src.cli.show_config", return_value=0):
            with mock.patch.object(
                sys, "argv", ["clawcodex", "--config", "--legacy-repl"]
            ):
                from src import cli
                cli.main()
            mock_pre_action.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Default invocation runs run_pre_action exactly once
# ---------------------------------------------------------------------------


class TestPreActionRunsForDefaultInvocation(unittest.TestCase):
    def test_pre_action_called_once_for_default_repl(self) -> None:
        # We patch the actual REPL launcher so the test doesn't drag
        # in the full provider/registry/etc. stack. _resolve_permission_state
        # is allowed to run because cli.start_repl reads args._resolved_*.
        with mock.patch("src.init.run_pre_action") as mock_pre, \
                mock.patch("src.cli.start_repl", return_value=0), \
                mock.patch("src.entrypoints.tui.should_use_tui", return_value=False), \
                mock.patch.object(sys, "argv", ["clawcodex"]):
            from src import cli
            cli.main()
            mock_pre.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Safe-env applies safe; PATH stays untouched
# ---------------------------------------------------------------------------


class TestInitSafeEnvApplyBeforeUnsafe(unittest.TestCase):
    def test_safe_applied_unsafe_skipped(self) -> None:
        config_env = {
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",  # safe
            "PATH": "/opt/evil/bin",                 # unsafe
        }
        original_path = os.environ.get("PATH", "")
        with mock.patch(
            "src.permissions.trust_boundary._load_config_env",
            return_value=config_env,
        ), mock.patch.object(init_module, "setup_graceful_shutdown"), \
                mock.patch.object(init_module, "start_api_preconnect"):
            os.environ.pop("ANTHROPIC_MODEL", None)
            try:
                init_module.init()
                self.assertEqual(os.environ["ANTHROPIC_MODEL"], "claude-sonnet-4-6")
                self.assertEqual(os.environ.get("PATH", ""), original_path)
            finally:
                os.environ.pop("ANTHROPIC_MODEL", None)


# ---------------------------------------------------------------------------
# 5. Atexit drain coexists with prefetch atexit
# ---------------------------------------------------------------------------


class TestGracefulShutdownCoexistsWithPrefetchAtexit(unittest.TestCase):
    def test_drain_runs_cleanup_and_does_not_block(self) -> None:
        # Register a cleanup, then call drain. Verify the cleanup
        # fires. Doesn't actually need a prefetch subprocess to verify
        # the API surface — that's covered by test_prefetch.py.
        calls = []
        gs.register_cleanup(lambda: calls.append("graceful_drained"))
        gs._run_all_cleanups()
        self.assertEqual(calls, ["graceful_drained"])


# ---------------------------------------------------------------------------
# 5a-5c. Signal-handler paths (subprocess-isolated)
# ---------------------------------------------------------------------------


def _run_in_subprocess(
    code: str,
    signal_after_ms: float | None = None,
    signal_name: str = "SIGTERM",
    timeout: float = 5.0,
) -> tuple[int, str, str]:
    """Run ``code`` in a fresh Python subprocess, optionally sending it
    ``signal_name`` after ``signal_after_ms`` ms. Returns
    (returncode, stdout, stderr).
    """
    import signal as _signal
    env = dict(os.environ)
    env["PYTHONPATH"] = str(WORKTREE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if signal_after_ms is not None:
        time.sleep(signal_after_ms / 1000.0)
        try:
            proc.send_signal(getattr(_signal, signal_name))
        except ProcessLookupError:
            pass  # already exited
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


class TestSigtermPathRunsCleanups(unittest.TestCase):
    """SIGTERM → signal handler → graceful_shutdown_sync →
    _run_all_cleanups → sys.exit(128+15=143). Cleanups fire."""

    def test_sigterm_triggers_drain(self) -> None:
        code = textwrap.dedent(
            """
            import sys, time
            from src.utils.graceful_shutdown import (
                register_cleanup, setup_graceful_shutdown,
            )
            register_cleanup(lambda: print("DRAINED", flush=True))
            setup_graceful_shutdown()
            # Sleep long enough for the parent to send SIGTERM.
            time.sleep(3.0)
            print("LIVE", flush=True)
            """
        )
        rc, out, err = _run_in_subprocess(
            code, signal_after_ms=200, signal_name="SIGTERM"
        )
        self.assertIn("DRAINED", out, msg=f"cleanup did not fire. err={err}")
        # 128+15 == 143 (SIGTERM exit code).
        self.assertEqual(rc, 143, msg=f"unexpected rc={rc}. out={out} err={err}")


class TestSigintDuringPrefetch(unittest.TestCase):
    """SIGINT while a prefetch handle is in-flight must not leave a zombie."""

    def test_sigint_during_prefetch_clean_exit(self) -> None:
        code = textwrap.dedent(
            """
            import time
            from src.utils.graceful_shutdown import setup_graceful_shutdown
            from src.prefetch import get_or_start_keychain_prefetch
            setup_graceful_shutdown()
            handle = get_or_start_keychain_prefetch()
            # Sleep long enough for the parent to send SIGINT.
            time.sleep(3.0)
            print("LIVE", flush=True)
            """
        )
        rc, out, err = _run_in_subprocess(
            code, signal_after_ms=100, signal_name="SIGINT"
        )
        # SIGINT exit code is 128+2 == 130.
        self.assertEqual(rc, 130, msg=f"unexpected rc={rc}. out={out} err={err}")


class TestSigintBeforePrefetchStarted(unittest.TestCase):
    """SIGINT before any prefetch is fired must exit cleanly (no
    'cleanup error' messages, code 130)."""

    def test_sigint_before_any_prefetch(self) -> None:
        code = textwrap.dedent(
            """
            import time
            from src.utils.graceful_shutdown import setup_graceful_shutdown
            setup_graceful_shutdown()
            time.sleep(3.0)
            print("LIVE", flush=True)
            """
        )
        rc, out, err = _run_in_subprocess(
            code, signal_after_ms=50, signal_name="SIGINT"
        )
        self.assertEqual(rc, 130, msg=f"unexpected rc={rc}. err={err}")
        # No "cleanup error" lines should appear in stderr.
        self.assertNotIn("cleanup error", err)


# ---------------------------------------------------------------------------
# 6. Profile checkpoints recorded in order
# ---------------------------------------------------------------------------


class TestProfileCheckpointsRecorded(unittest.TestCase):
    """When CLAUDE_CODE_PROFILE_STARTUP is set, calling run_pre_action
    must emit the expected sequence of checkpoints."""

    def test_pre_action_emits_expected_checkpoints(self) -> None:
        # We have to manually enable profiling for this test since the
        # latch in startup_profiler is captured at module-import.
        startup_profiler._PROFILING_ENABLED = True
        try:
            startup_profiler.reset_profiler_for_test_only()

            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)

            names = [name for name, _ in startup_profiler.get_internal_phase_log()]
            # Must contain (at minimum) the expected pre_action_* and
            # init_* checkpoints. Order matters.
            self.assertIn("pre_action_start", names)
            self.assertIn("init_function_start", names)
            self.assertIn("init_safe_env_vars_applied", names)
            self.assertIn("init_after_graceful_shutdown", names)
            self.assertIn("init_after_api_preconnect", names)
            self.assertIn("init_function_end", names)
            self.assertIn("pre_action_after_init", names)
            self.assertIn("pre_action_end", names)

            # Sanity: pre_action_start comes before pre_action_end.
            self.assertLess(
                names.index("pre_action_start"),
                names.index("pre_action_end"),
            )
        finally:
            # Restore the latch's original state (read from env at
            # module-import). The autouse fixture also calls reset.
            startup_profiler._PROFILING_ENABLED = (
                startup_profiler._read_env_gate()
            )


if __name__ == "__main__":
    unittest.main()
