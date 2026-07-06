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


class TestHardGateRefusesToStart:
    """critic C8-MAJOR: failIfUnavailable is a REFUSE-TO-START, not a per-bash
    refusal — so /bg + MCP + hooks (which run OUTSIDE _bash_call) don't leak.
    Drive the REAL _build_runtime under a hard-gate config → sess.init_error
    is set (the session refuses to start)."""

    def test_build_runtime_refuses_under_hard_gate(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )
        from src.settings.types import SettingsSchema

        hard = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: hard)

        sess = _AgentSession(
            session_id="s1", cwd=str(tmp_path),
            config=AgentServerConfig(provider_name="anthropic", single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        assert sess.init_error is not None
        assert "sandbox" in sess.init_error.lower()
        # refused BEFORE building tools/provider — no runtime to leak /bg through
        assert sess.tool_registry is None

    def test_build_runtime_starts_normally_without_hard_gate(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )
        from src.settings.types import SettingsSchema

        # warning-only (enabled, not failIfUnavailable) must NOT refuse to start
        warn = SettingsSchema.from_dict({"sandbox": {"enabled": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: warn)

        sess = _AgentSession(
            session_id="s2", cwd=str(tmp_path),
            config=AgentServerConfig(provider_name="definitely-not-a-provider", single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        # it may fail for the unknown provider, but NOT with a sandbox gate
        assert sess.init_error is None or "sandbox" not in sess.init_error.lower()

    def test_bg_run_control_request_refused_under_init_error(self, monkeypatch, tmp_path):
        """critic C8 re-review: /bg is a CONTROL request that bypasses
        _build_runtime + the turn path, so a refused-to-start session (init_error
        set) must REFUSE bg_run — else it spawns an unsandboxed subprocess. Drive
        the real _handle_control_request and assert no subprocess + the gate
        error reply."""
        import asyncio
        import subprocess
        from unittest.mock import MagicMock

        from src.server.agent_server import AgentServerConfig, _AgentSession

        sess = _AgentSession(
            session_id="s1", cwd=str(tmp_path),
            config=AgentServerConfig(provider_name="anthropic", single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess.init_error = "sandbox hard gate: refusing to start"

        replies = []
        monkeypatch.setattr(sess, "_reply", lambda rid, payload: replies.append((rid, payload)))
        # if the guard fails, _do_bgtask would run — trip both it and Popen
        monkeypatch.setattr(subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("subprocess spawned under init_error — /bg leaked!")))
        monkeypatch.setattr(sess, "_do_bgtask",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("_do_bgtask reached under init_error")))

        asyncio.run(sess._handle_control_request({
            "request_id": "r1",
            "request": {"subtype": "bg_run", "command": "echo leaked"},
        }))
        assert replies and replies[0][0] == "r1"
        assert replies[0][1]["ok"] is False
        assert "sandbox" in replies[0][1]["error"].lower()

    def test_interrupt_still_works_under_init_error(self, monkeypatch, tmp_path):
        # interrupt is exempt (benign abort) — must NOT be refused with the gate error
        import asyncio
        from unittest.mock import MagicMock

        from src.server.agent_server import AgentServerConfig, _AgentSession

        sess = _AgentSession(
            session_id="s2", cwd=str(tmp_path),
            config=AgentServerConfig(provider_name="anthropic", single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess.init_error = "sandbox hard gate"
        replies = []
        monkeypatch.setattr(sess, "_reply", lambda rid, payload: replies.append((rid, payload)))
        asyncio.run(sess._handle_control_request({
            "request_id": "r2", "request": {"subtype": "interrupt"},
        }))
        assert not any("sandbox" in str(r[1].get("error", "")).lower() for r in replies)


class TestSkillShellFailsClosed:
    """minor #4: a skill/slash `!`-embedded shell runs through _bash_call, so
    the hard gate refuses it too (fail-closed)."""

    def test_skill_shell_refused_under_hard_gate(self, monkeypatch, tmp_path):
        from src.settings.types import SettingsSchema
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.errors import ToolPermissionError
        from src.tool_system.tools.bash.bash_tool import _bash_call

        hard = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: hard)
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        # the skill !-shell path is just _bash_call with a command — must refuse
        with pytest.raises(ToolPermissionError):
            _bash_call({"command": "echo from-skill-shell"}, ctx)


class TestEnabledPlatforms:
    """minor #3: TS enabledPlatforms — sandbox is disabled (no gate/warning)
    on a platform not in the list. The port was more aggressive; now faithful."""

    def test_other_platform_disables_hard_gate(self, monkeypatch):
        from src.permissions import sandbox_guard
        from src.settings.types import SettingsSchema

        # a hard-gate config restricted to a platform we're NOT on → no gate
        monkeypatch.setattr(sandbox_guard, "_current_platform", lambda: "linux")
        s = SettingsSchema.from_dict({"sandbox": {
            "enabled": True, "failIfUnavailable": True, "enabledPlatforms": ["macos"],
        }})
        assert sandbox_guard.sandbox_hard_gate_error(s) is None
        assert sandbox_guard.sandbox_unsandboxed_warning(s) is None

    def test_current_platform_listed_still_gates(self, monkeypatch):
        from src.permissions import sandbox_guard
        from src.settings.types import SettingsSchema

        monkeypatch.setattr(sandbox_guard, "_current_platform", lambda: "macos")
        s = SettingsSchema.from_dict({"sandbox": {
            "enabled": True, "failIfUnavailable": True, "enabledPlatforms": ["macos"],
        }})
        assert sandbox_guard.sandbox_hard_gate_error(s) is not None

    def test_empty_platforms_means_all(self, monkeypatch):
        from src.permissions import sandbox_guard
        from src.settings.types import SettingsSchema

        monkeypatch.setattr(sandbox_guard, "_current_platform", lambda: "windows")
        s = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        assert sandbox_guard.sandbox_hard_gate_error(s) is not None
