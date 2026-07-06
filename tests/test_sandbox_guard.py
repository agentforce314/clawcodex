"""Chapter C8 — sandbox guard (the silent-unsandboxed footgun).

The port has no sandbox ENFORCEMENT and previously didn't even PARSE
settings.sandbox — a user's sandbox config was silently dropped AND unenforced
(false sense of security). These pin: the settings parse (camelCase + unknown-
key passthrough), and the guard mapping onto TS's documented sandbox-unavailable
path (sandboxTypes.ts:96-103) — hard-gate refusal when failIfUnavailable, warn
otherwise, silent when off.
"""
from __future__ import annotations

import logging

import pytest

from src.permissions.sandbox_guard import (
    is_sandbox_requested,
    sandbox_hard_gate_error,
    sandbox_unsandboxed_warning,
    warn_if_unsandboxed_once,
)
from src.settings.types import SandboxSettings, SettingsSchema
from src.settings.validation import validate_settings


class TestParse:
    def test_camelcase_object_parsed_not_dropped(self):
        s = SettingsSchema.from_dict({"sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": False,
            "allowUnsandboxedCommands": False,
            "excludedCommands": ["git status", "ls"],
        }})
        assert isinstance(s.sandbox, SandboxSettings)
        assert s.sandbox.enabled and s.sandbox.fail_if_unavailable
        assert s.sandbox.auto_allow_bash_if_sandboxed is False
        assert s.sandbox.allow_unsandboxed_commands is False
        assert s.sandbox.excluded_commands == ["git status", "ls"]

    def test_unknown_enforcement_keys_ignored(self):
        # TS schema is .passthrough() with network/filesystem/ignoreViolations
        # etc. the port doesn't act on — must not crash the load.
        s = SettingsSchema.from_dict({"sandbox": {
            "enabled": True, "network": {"allowAll": True}, "ripgrep": {"command": "rg"},
        }})
        assert s.sandbox.enabled

    def test_absent_sandbox_is_none(self):
        assert SettingsSchema.from_dict({}).sandbox is None


class TestGuardMapping:
    def _s(self, **sb):
        return SettingsSchema.from_dict({"sandbox": sb}) if sb else SettingsSchema.from_dict({})

    def test_hard_gate_when_enabled_and_fail_if_unavailable(self):
        s = self._s(enabled=True, failIfUnavailable=True)
        assert sandbox_hard_gate_error(s) is not None
        assert sandbox_unsandboxed_warning(s) is None  # gate, not warning
        assert "hard gate" in sandbox_hard_gate_error(s).lower()

    def test_warning_when_enabled_only(self):
        s = self._s(enabled=True)
        assert sandbox_hard_gate_error(s) is None
        assert "unsandboxed" in sandbox_unsandboxed_warning(s).lower()

    def test_silent_when_disabled(self):
        s = self._s(enabled=False, failIfUnavailable=True)  # enabled gates both
        assert sandbox_hard_gate_error(s) is None
        assert sandbox_unsandboxed_warning(s) is None

    def test_silent_when_absent(self):
        s = self._s()
        assert not is_sandbox_requested(s)
        assert sandbox_hard_gate_error(s) is None
        assert sandbox_unsandboxed_warning(s) is None


class TestValidateSettings:
    def test_hard_gate_is_a_validation_error(self):
        s = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        assert any(e.field == "sandbox" for e in validate_settings(s))

    def test_warning_only_does_not_invalidate(self):
        # enabled without failIfUnavailable must still LOAD (TS runs with a warning)
        s = SettingsSchema.from_dict({"sandbox": {"enabled": True}})
        assert not any(e.field == "sandbox" for e in validate_settings(s))


class TestWarnOnce:
    def test_warns_once(self, caplog):
        import src.permissions.sandbox_guard as mod

        mod._warned_once = False
        s = SettingsSchema.from_dict({"sandbox": {"enabled": True}})
        with caplog.at_level(logging.WARNING, logger="src.permissions.sandbox_guard"):
            warn_if_unsandboxed_once(s)
            warn_if_unsandboxed_once(s)
        assert sum("UNSANDBOXED" in m for m in caplog.messages) == 1


class TestBashHardGateRefusal:
    def test_bash_refuses_under_hard_gate(self, monkeypatch, tmp_path):
        # the guaranteed-reached path: a hard-gate config makes _bash_call REFUSE
        from src.settings.types import SettingsSchema
        from src.tool_system.errors import ToolPermissionError

        hard = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: hard)

        from src.tool_system.tools.bash.bash_tool import _bash_call
        from src.tool_system.context import ToolContext, ToolUseOptions

        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        with pytest.raises(ToolPermissionError, match="hard gate|unsandboxed"):
            _bash_call({"command": "echo hi"}, ctx)

    def test_bash_runs_with_warning_when_not_hard_gate(self, monkeypatch, tmp_path):
        import src.permissions.sandbox_guard as mod

        mod._warned_once = False
        warn_only = SettingsSchema.from_dict({"sandbox": {"enabled": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: warn_only)

        from src.tool_system.tools.bash.bash_tool import _bash_call
        from src.tool_system.context import ToolContext, ToolUseOptions

        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        res = _bash_call({"command": "echo sandbox-ok"}, ctx)
        assert res.is_error is False
        assert "sandbox-ok" in res.output["stdout"]  # ran unsandboxed, with the warning


class TestBackgroundBashAlsoGuarded:
    """The hard gate must cover BACKGROUND bash too — else it's a false gate.
    Both fg + bg flow through _bash_call; the guard precedes the
    run_in_background branch."""

    def test_background_bash_refused_under_hard_gate(self, monkeypatch, tmp_path):
        from src.settings.types import SettingsSchema
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.errors import ToolPermissionError
        from src.tool_system.tools.bash.bash_tool import _bash_call

        hard = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: hard)
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        with pytest.raises(ToolPermissionError):
            _bash_call({"command": "sleep 100", "run_in_background": True}, ctx)
