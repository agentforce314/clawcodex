"""Unit tests for ``src/replLauncher.py:launch_repl`` (plan phase 3).

Verifies that the chapter phase-4 dispatch logic (previously inline in
``cli.main()``) is correctly factored into ``launch_repl(args)`` and
delegates to the right mode runner.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import pytest

from src.bootstrap.state import reset_state_for_tests
from src.replLauncher import build_repl_banner, launch_repl


@pytest.fixture(autouse=True)
def _reset_state():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


def _make_args(**kwargs) -> types.SimpleNamespace:
    """Build a minimal argparse-ish namespace for tests."""
    defaults = dict(
        print=False,
        tui=False,
        legacy_repl=False,
        no_tui=False,
        stream=False,
        _resolved_permission_mode="default",
        _resolved_is_bypass_available=False,
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


class TestBuildReplBanner(unittest.TestCase):
    def test_banner_returns_string(self) -> None:
        banner = build_repl_banner()
        self.assertIsInstance(banner, str)
        self.assertGreater(len(banner), 0)


class TestLaunchReplPrintMode(unittest.TestCase):
    """``args.print=True`` must dispatch to print mode unconditionally."""

    def test_print_mode_dispatches_to_run_print_mode(self) -> None:
        with mock.patch("src.cli._run_print_mode", return_value=0) as mock_print, \
                mock.patch("src.cli._run_tui_mode") as mock_tui, \
                mock.patch("src.cli.start_repl") as mock_repl:
            rc = launch_repl(_make_args(print=True))
        self.assertEqual(rc, 0)
        mock_print.assert_called_once()
        mock_tui.assert_not_called()
        mock_repl.assert_not_called()

    def test_print_mode_overrides_tui_flag(self) -> None:
        # When --print is set, --tui is ignored. Mirrors TS's
        # main.tsx behavior.
        with mock.patch("src.cli._run_print_mode", return_value=0) as mock_print, \
                mock.patch("src.cli._run_tui_mode") as mock_tui:
            launch_repl(_make_args(print=True, tui=True))
        mock_print.assert_called_once()
        mock_tui.assert_not_called()


class TestLaunchReplTuiMode(unittest.TestCase):
    def test_explicit_tui_flag_dispatches_to_tui(self) -> None:
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=True), \
                mock.patch("src.cli._run_tui_mode", return_value=0) as mock_tui, \
                mock.patch("src.cli.start_repl") as mock_repl:
            rc = launch_repl(_make_args(tui=True))
        self.assertEqual(rc, 0)
        mock_tui.assert_called_once()
        mock_repl.assert_not_called()

    def test_legacy_repl_flag_forces_repl(self) -> None:
        # --legacy-repl forces the REPL branch even if should_use_tui
        # would otherwise return True.
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=False) as mock_should, \
                mock.patch("src.cli.start_repl", return_value=0) as mock_repl:
            launch_repl(_make_args(legacy_repl=True))
        mock_should.assert_called_once_with(False)
        mock_repl.assert_called_once()

    def test_no_tui_flag_forces_repl(self) -> None:
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=False) as mock_should, \
                mock.patch("src.cli.start_repl", return_value=0):
            launch_repl(_make_args(no_tui=True))
        mock_should.assert_called_once_with(False)


class TestLaunchReplDefaultMode(unittest.TestCase):
    """When no flag is set, auto-detect (defer to should_use_tui)."""

    def test_default_falls_through_to_should_use_tui(self) -> None:
        # When neither --tui nor --legacy-repl nor --no-tui is set,
        # the launcher calls should_use_tui(None) to let it auto-detect.
        with mock.patch(
            "src.entrypoints.tui.should_use_tui", return_value=False
        ) as mock_should, mock.patch("src.cli.start_repl", return_value=0):
            launch_repl(_make_args())
        mock_should.assert_called_once_with(None)

    def test_auto_detected_tui_dispatches_to_tui(self) -> None:
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=True), \
                mock.patch("src.cli._run_tui_mode", return_value=0) as mock_tui:
            launch_repl(_make_args())
        mock_tui.assert_called_once()


class TestLaunchReplProfileCheckpoints(unittest.TestCase):
    """Each mode branch must emit phase4_dispatch + its mode_dispatch_*
    checkpoint."""

    def setUp(self) -> None:
        from src.utils import startup_profiler
        self._was_enabled = startup_profiler._PROFILING_ENABLED
        startup_profiler._PROFILING_ENABLED = True
        startup_profiler.reset_profiler_for_test_only()

    def tearDown(self) -> None:
        from src.utils import startup_profiler
        startup_profiler._PROFILING_ENABLED = self._was_enabled
        startup_profiler.reset_profiler_for_test_only()

    def test_print_mode_checkpoints(self) -> None:
        from src.utils import startup_profiler
        with mock.patch("src.cli._run_print_mode", return_value=0):
            launch_repl(_make_args(print=True))
        names = [n for n, _ in startup_profiler.get_internal_phase_log()]
        self.assertIn("mode_dispatch_print", names)
        self.assertIn("phase4_dispatch", names)

    def test_tui_mode_checkpoints(self) -> None:
        from src.utils import startup_profiler
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=True), \
                mock.patch("src.cli._run_tui_mode", return_value=0):
            launch_repl(_make_args(tui=True))
        names = [n for n, _ in startup_profiler.get_internal_phase_log()]
        self.assertIn("mode_dispatch_tui", names)
        self.assertIn("phase4_dispatch", names)

    def test_repl_mode_checkpoints(self) -> None:
        from src.utils import startup_profiler
        with mock.patch("src.entrypoints.tui.should_use_tui", return_value=False), \
                mock.patch("src.cli.start_repl", return_value=0):
            launch_repl(_make_args())
        names = [n for n, _ in startup_profiler.get_internal_phase_log()]
        self.assertIn("mode_dispatch_repl", names)
        self.assertIn("phase4_dispatch", names)


class TestCliMainCallsLauncher(unittest.TestCase):
    """Integration: cli.main now calls launch_repl(args) instead of
    inline mode dispatch. Verify the wiring."""

    def test_cli_main_invokes_launch_repl(self) -> None:
        with mock.patch("src.init.run_pre_action"), \
                mock.patch("src.cli._resolve_permission_state"), \
                mock.patch("src.setup.run_production_setup"), \
                mock.patch("src.replLauncher.launch_repl", return_value=0) as mock_launch, \
                mock.patch.object(sys, "argv", ["clawcodex"]):
            from src import cli
            cli.main()
            mock_launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
