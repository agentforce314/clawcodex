"""Unit tests for ``src/setup.py:run_production_setup`` (plan phase 2).

The parity-audit ``run_setup`` function is covered by the existing
test suite (tests via the ``setup-report`` subcommand of main.py).
This file covers the new production primitive.
"""

from __future__ import annotations

import logging
import sys
import unittest
from unittest import mock

import pytest

from src.bootstrap.state import reset_state_for_tests
from src.hooks.snapshot import reset_hook_snapshot_for_test_only
from src.setup import (
    MIN_PYTHON_VERSION,
    _capture_hook_snapshot,
    _check_python_version,
    _emit_tengu_exit_previous_session,
    _emit_tengu_started_beacon,
    run_production_setup,
)


@pytest.fixture(autouse=True)
def _reset_phase2_state():
    reset_state_for_tests()
    reset_hook_snapshot_for_test_only()
    yield
    reset_state_for_tests()
    reset_hook_snapshot_for_test_only()


class TestPythonVersionCheck(unittest.TestCase):
    def test_passes_on_current_python(self) -> None:
        # Current interpreter must be >= MIN_PYTHON_VERSION (test
        # infrastructure itself requires it). Verifies the happy path.
        _check_python_version()  # must not raise

    def test_fails_on_low_version(self) -> None:
        # Simulate an old Python. patch sys.version_info to a tuple-like.
        class FakeVersionInfo:
            def __getitem__(self, idx):
                return (3, 9, 0)[idx]

            def __init__(self):
                self.major = 3
                self.minor = 9
                self.micro = 0

        with mock.patch.object(sys, "version_info", FakeVersionInfo()):
            with self.assertRaises(SystemExit) as ctx:
                _check_python_version()
            self.assertEqual(ctx.exception.code, 1)


class TestHookSnapshotCapture(unittest.TestCase):
    def test_capture_idempotent(self) -> None:
        # First call loads; second returns same instance.
        from src.hooks.snapshot import (
            capture_hooks_config_snapshot,
            get_active_hook_config_manager,
        )
        manager1 = capture_hooks_config_snapshot()
        manager2 = capture_hooks_config_snapshot()
        self.assertIs(manager1, manager2)
        self.assertIs(get_active_hook_config_manager(), manager1)

    def test_returns_manager_with_snapshot(self) -> None:
        from src.hooks.snapshot import capture_hooks_config_snapshot
        manager = capture_hooks_config_snapshot()
        # snapshot attribute exists and is non-None after load.
        self.assertIsNotNone(manager.snapshot)

    def test_setup_step_does_not_raise_on_missing_settings(self) -> None:
        # _capture_hook_snapshot is best-effort: even if the snapshot
        # loader fails, setup continues.
        with mock.patch(
            "src.hooks.snapshot.capture_hooks_config_snapshot",
            side_effect=RuntimeError("disk error"),
        ):
            _capture_hook_snapshot()  # must not raise


class TestTenguStartedBeacon(unittest.TestCase):
    def test_emits_info_log(self) -> None:
        with self.assertLogs("clawcodex.setup", level="INFO") as ctx:
            _emit_tengu_started_beacon()
        self.assertTrue(any("tengu_started" in line for line in ctx.output))


class TestTenguExitPreviousSession(unittest.TestCase):
    def test_no_op_when_no_previous_session(self) -> None:
        # Fresh process: cost = 0, api_ms = 0, tool_ms = 0 → no emit.
        logger = logging.getLogger("clawcodex.setup")
        with mock.patch.object(logger, "info") as mock_info:
            _emit_tengu_exit_previous_session()
            mock_info.assert_not_called()

    def test_emits_when_previous_session_data_present(self) -> None:
        # Restore non-zero cost state (simulates resumed session).
        from src.bootstrap.state import (
            ModelUsage,
            set_cost_state_for_restore,
        )
        set_cost_state_for_restore(
            total_cost_usd=1.23,
            total_api_duration=100,
            total_api_duration_without_retries=80,
            total_tool_duration=50,
            total_lines_added=0,
            total_lines_removed=0,
            model_usage={"claude-sonnet-4-6": ModelUsage(input_tokens=10)},
        )
        with self.assertLogs("clawcodex.setup", level="INFO") as ctx:
            _emit_tengu_exit_previous_session()
        # Look for tengu_exit in output.
        self.assertTrue(
            any("tengu_exit" in line for line in ctx.output),
            f"output was {ctx.output}",
        )


class TestSnapshotReachableFromProductionContext(unittest.TestCase):
    """Major #3 from round-2 critic review: assert that the captured
    hook snapshot AND the session-trust flag flow into a
    freshly-constructed ToolContext via __post_init__.

    This is the wiring invariant that closes C3.2 from the gap analysis
    ("No captureHooksConfigSnapshot — this is a security regression").
    Without this test, a future regression could un-wire the auto-
    populate logic and the snapshot would silently become dead
    infrastructure again.
    """

    def test_tool_context_auto_populates_hook_manager(self) -> None:
        # Pre-condition: snapshot is captured.
        from src.hooks.snapshot import (
            capture_hooks_config_snapshot,
            get_active_hook_config_manager,
        )
        capture_hooks_config_snapshot()
        captured_manager = get_active_hook_config_manager()
        self.assertIsNotNone(
            captured_manager,
            "test setup invariant: snapshot must be captured first",
        )

        # Act: construct a ToolContext via the production constructor.
        from pathlib import Path

        from src.tool_system.context import ToolContext
        ctx = ToolContext(workspace_root=Path.cwd())

        # Assert: hook_config_manager auto-populates from the snapshot.
        self.assertIs(
            ctx.hook_config_manager,
            captured_manager,
            "ToolContext.__post_init__ must read the captured snapshot",
        )

    def test_tool_context_auto_populates_workspace_trusted(self) -> None:
        # Pre-condition: run_pre_action has set trust accepted = True.
        # (Phase 1 A6 working assumption.)
        from src.bootstrap.state import (
            get_session_trust_accepted,
            set_session_trust_accepted,
        )
        set_session_trust_accepted(True)
        self.assertTrue(
            get_session_trust_accepted(),
            "test setup invariant: trust must be accepted first",
        )

        # Act + Assert.
        from pathlib import Path

        from src.tool_system.context import ToolContext
        ctx = ToolContext(workspace_root=Path.cwd())
        self.assertTrue(
            ctx.workspace_trusted,
            "ToolContext.__post_init__ must read get_session_trust_accepted",
        )

    def test_auto_populate_fires_when_field_at_default(self) -> None:
        # Auto-populate fires when the field is at its dataclass
        # default (None for hook_config_manager, False for
        # workspace_trusted). Callers that explicitly pass non-default
        # values are NOT overridden — but callers cannot opt out by
        # passing the default value, because the current ``is None`` /
        # ``is False`` guard cannot distinguish "not passed" from
        # "explicitly passed default."
        #
        # For plan phase 2 (trust is implicit, no opt-out path needed)
        # this is acceptable. When the trust dialog ships in plan phase
        # 3, revisit with a sentinel-based design that disambiguates
        # explicit-default from not-passed:
        #
        #     _UNSET = object()
        #     workspace_trusted: object = _UNSET
        #     if self.workspace_trusted is _UNSET:
        #         self.workspace_trusted = get_session_trust_accepted()
        from pathlib import Path

        from src.tool_system.context import ToolContext

        from src.bootstrap.state import set_session_trust_accepted
        set_session_trust_accepted(True)

        ctx = ToolContext(workspace_root=Path.cwd())
        # The auto-populate fires because state was True.
        self.assertTrue(ctx.workspace_trusted)


class TestRunProductionSetupIntegration(unittest.TestCase):
    """End-to-end: run_production_setup runs all substeps in order."""

    def test_runs_all_substeps_in_order(self) -> None:
        call_log: list[str] = []
        with mock.patch(
            "src.setup._check_python_version",
            side_effect=lambda: call_log.append("python_check"),
        ), mock.patch(
            "src.setup._capture_hook_snapshot",
            side_effect=lambda: call_log.append("snapshot"),
        ), mock.patch(
            "src.setup._emit_tengu_started_beacon",
            side_effect=lambda: call_log.append("started"),
        ), mock.patch(
            "src.setup._emit_tengu_exit_previous_session",
            side_effect=lambda: call_log.append("exit"),
        ):
            run_production_setup(args=None)

        self.assertEqual(
            call_log,
            ["python_check", "snapshot", "started", "exit"],
            "setup substeps must run in chapter order",
        )

    def test_python_version_failure_aborts_setup(self) -> None:
        # If the Python-version check raises SystemExit, the
        # subsequent substeps must NOT run.
        with mock.patch(
            "src.setup._check_python_version",
            side_effect=SystemExit(1),
        ), mock.patch(
            "src.setup._capture_hook_snapshot",
        ) as mock_snapshot:
            with self.assertRaises(SystemExit):
                run_production_setup(args=None)
            mock_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
