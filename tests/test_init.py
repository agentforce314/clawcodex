"""Unit tests for ``src/init.py`` (P1.3, P1.4, P1.6).

Verifies the memoize property, substep ordering, run_pre_action
behavior, and the interactive-state setters.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

import pytest

from src import init as init_module
from src.bootstrap.state import (
    get_client_type,
    get_is_interactive,
    get_session_trust_accepted,
    reset_state_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_init_and_state():
    """Reset the init memoize cache and bootstrap state per test."""
    init_module.reset_init_for_test_only()
    reset_state_for_tests()
    # Also reset graceful_shutdown so signal handlers aren't sticky.
    from src.utils import graceful_shutdown as gs
    gs.reset_for_test_only()
    yield
    init_module.reset_init_for_test_only()
    reset_state_for_tests()
    gs.reset_for_test_only()


class TestInitRunsSubstepsInOrder(unittest.TestCase):
    """init() must call its substeps in the chapter's documented order."""

    def test_substep_call_order(self) -> None:
        call_log: list[str] = []
        with mock.patch.object(
            init_module,
            "apply_safe_config_environment_variables",
            side_effect=lambda *a, **kw: call_log.append("safe_env"),
        ), mock.patch.object(
            init_module,
            "setup_graceful_shutdown",
            side_effect=lambda *a, **kw: call_log.append("graceful_shutdown"),
        ), mock.patch.object(
            init_module,
            "start_api_preconnect",
            side_effect=lambda *a, **kw: call_log.append("api_preconnect"),
        ):
            init_module.init()

        self.assertEqual(
            call_log,
            ["safe_env", "graceful_shutdown", "api_preconnect"],
            "init() substeps must run in chapter order",
        )


class TestInitIsMemoized(unittest.TestCase):
    """The @cache decorator ensures the substeps run exactly once per
    process, regardless of how many callers invoke init()."""

    def test_three_calls_run_substeps_once(self) -> None:
        with mock.patch.object(
            init_module, "apply_safe_config_environment_variables"
        ) as mock_safe, mock.patch.object(
            init_module, "setup_graceful_shutdown"
        ) as mock_shutdown, mock.patch.object(
            init_module, "start_api_preconnect"
        ) as mock_preconnect:
            init_module.init()
            init_module.init()
            init_module.init()

            self.assertEqual(mock_safe.call_count, 1)
            self.assertEqual(mock_shutdown.call_count, 1)
            self.assertEqual(mock_preconnect.call_count, 1)


class TestResetClearsCache(unittest.TestCase):
    def test_reset_re_runs_substeps(self) -> None:
        with mock.patch.object(
            init_module, "apply_safe_config_environment_variables"
        ) as mock_safe:
            init_module.init()
            init_module.reset_init_for_test_only()
            init_module.init()
            self.assertEqual(mock_safe.call_count, 2)

    def test_reset_outside_pytest_raises(self) -> None:
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with self.assertRaises(RuntimeError):
                init_module.reset_init_for_test_only()
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved


class TestInitDoesNotApplyUnsafeEnv(unittest.TestCase):
    """End-to-end: init() must NOT touch PATH or other unsafe vars."""

    def test_path_is_not_modified_by_init(self) -> None:
        # Setup: pretend the user's global config has both safe and
        # unsafe keys. apply_safe_config_environment_variables (real,
        # not mocked) should only apply the safe one.
        config_env = {
            "PATH": "/opt/evil/bin",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        }
        original_path = os.environ.get("PATH", "")

        with mock.patch(
            "src.permissions.trust_boundary._load_config_env",
            return_value=config_env,
        ), mock.patch.object(init_module, "setup_graceful_shutdown"), \
                mock.patch.object(init_module, "start_api_preconnect"):
            os.environ.pop("ANTHROPIC_MODEL", None)
            init_module.init()

        try:
            # PATH unchanged.
            self.assertEqual(os.environ.get("PATH", ""), original_path)
            # ANTHROPIC_MODEL was applied (safe).
            self.assertEqual(os.environ.get("ANTHROPIC_MODEL"), "claude-sonnet-4-6")
        finally:
            os.environ.pop("ANTHROPIC_MODEL", None)


class TestRunPreActionCallsInit(unittest.TestCase):
    def test_pre_action_invokes_init(self) -> None:
        with mock.patch.object(init_module, "init") as mock_init:
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            mock_init.assert_called_once()


class TestRunPreActionSetsInteractive(unittest.TestCase):
    def test_default_args_interactive_true_when_tty(self) -> None:
        # We can't make sys.stdout a real TTY in unittest, so patch
        # isatty to return True.
        with mock.patch.object(init_module, "init"), \
                mock.patch.object(sys.stdout, "isatty", return_value=True):
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            self.assertTrue(get_is_interactive())

    def test_print_mode_sets_interactive_false(self) -> None:
        with mock.patch.object(init_module, "init"):
            args = types.SimpleNamespace(print=True)
            init_module.run_pre_action(args)
            self.assertFalse(get_is_interactive())

    def test_non_tty_stdout_sets_interactive_false(self) -> None:
        with mock.patch.object(init_module, "init"), \
                mock.patch.object(sys.stdout, "isatty", return_value=False):
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            self.assertFalse(get_is_interactive())


class TestRunPreActionSetsClientType(unittest.TestCase):
    def test_default_when_env_unset(self) -> None:
        with mock.patch.object(init_module, "init"), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            self.assertEqual(get_client_type(), "cli")

    def test_sdk_py_override(self) -> None:
        with mock.patch.object(init_module, "init"), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}):
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            self.assertEqual(get_client_type(), "sdk-py")

    def test_unknown_value_falls_back_to_cli(self) -> None:
        # Defensive default: an attacker setting this env var to a
        # random string shouldn't change behavior.
        with mock.patch.object(init_module, "init"), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "totally-random"}):
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
            self.assertEqual(get_client_type(), "cli")


class TestRunPreActionSetsTrustAccepted(unittest.TestCase):
    """A6 / P1.6: plan-phase-1 default is `set_session_trust_accepted(True)`
    so existing trust-gate consumers behave correctly."""

    def test_pre_action_sets_trust_accepted_true(self) -> None:
        # Pre-state: default False.
        self.assertFalse(get_session_trust_accepted())
        with mock.patch.object(init_module, "init"):
            args = types.SimpleNamespace(print=False)
            init_module.run_pre_action(args)
        self.assertTrue(get_session_trust_accepted())


if __name__ == "__main__":
    unittest.main()
