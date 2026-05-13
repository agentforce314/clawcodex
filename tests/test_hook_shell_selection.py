"""Tests for per-hook shell selection — Chapter 12 round 2.

Covers ``HookConfig.shell``, settings.json parsing, validator behaviour, and
the executor branch that swaps between ``create_subprocess_shell`` (bash) and
``create_subprocess_exec(pwsh, ...)`` (powershell). Mirrors TS coverage of
``shell`` in ``schemas/hooks.ts`` and ``utils/hooks.ts``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from src.hooks.config_manager import (
    _parse_hook_config,
    load_hooks_from_settings,
    validate_hook_configs,
)
from src.hooks.hook_executor import _execute_command_hook
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.shell_invocation import (
    DEFAULT_HOOK_SHELL,
    SHELL_TYPES,
    build_powershell_args,
    find_powershell_path,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestShellInvocationConstants:
    def test_shell_types_lists_bash_first(self) -> None:
        # Bash first matches TS shipping order (typescript/src/utils/shell/shellProvider.ts:1).
        assert SHELL_TYPES == ("bash", "powershell")

    def test_default_is_bash(self) -> None:
        assert DEFAULT_HOOK_SHELL == "bash"


# ---------------------------------------------------------------------------
# build_powershell_args
# ---------------------------------------------------------------------------


class TestBuildPowerShellArgs:
    def test_exact_argv_matches_ts(self) -> None:
        # Matches typescript/src/utils/shell/powershellProvider.ts:11-13 exactly.
        args = build_powershell_args("Write-Host hi")
        assert args == ["-NoProfile", "-NonInteractive", "-Command", "Write-Host hi"]

    def test_preserves_command_string_verbatim(self) -> None:
        # No quoting / escaping — pwsh's -Command consumes the literal string.
        cmd = "Get-Content 'a b'; echo \"$x\""
        args = build_powershell_args(cmd)
        assert args[-1] == cmd
        assert args[:3] == ["-NoProfile", "-NonInteractive", "-Command"]

    def test_empty_command_is_still_legal_argv(self) -> None:
        # No validation here — the executor's path-missing check fires first.
        assert build_powershell_args("") == ["-NoProfile", "-NonInteractive", "-Command", ""]


# ---------------------------------------------------------------------------
# find_powershell_path
# ---------------------------------------------------------------------------


class TestFindPowerShellPath:
    def test_prefers_pwsh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_which(name: str) -> str | None:
            calls.append(name)
            return "/usr/local/bin/pwsh" if name == "pwsh" else None

        monkeypatch.setattr("src.hooks.shell_invocation.shutil.which", fake_which)
        assert find_powershell_path() == "/usr/local/bin/pwsh"
        # Doesn't bother checking "powershell" if "pwsh" hits.
        assert calls == ["pwsh"]

    def test_falls_back_to_powershell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_which(name: str) -> str | None:
            return r"C:\Windows\System32\powershell.exe" if name == "powershell" else None

        monkeypatch.setattr("src.hooks.shell_invocation.shutil.which", fake_which)
        assert find_powershell_path() == r"C:\Windows\System32\powershell.exe"

    def test_returns_none_when_neither_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.hooks.shell_invocation.shutil.which", lambda _name: None)
        assert find_powershell_path() is None


# ---------------------------------------------------------------------------
# HookConfig.shell default
# ---------------------------------------------------------------------------


class TestHookConfigShellField:
    def test_default_is_none(self) -> None:
        # None == "use the platform default" (bash on POSIX).
        hook = HookConfig(type="command", command="echo hi")
        assert hook.shell is None

    def test_explicit_bash(self) -> None:
        hook = HookConfig(type="command", command="echo hi", shell="bash")
        assert hook.shell == "bash"

    def test_explicit_powershell(self) -> None:
        hook = HookConfig(type="command", command="Write-Host hi", shell="powershell")
        assert hook.shell == "powershell"


# ---------------------------------------------------------------------------
# Settings.json parsing — _parse_hook_config + load_hooks_from_settings
# ---------------------------------------------------------------------------


class TestParseHookConfigShell:
    def test_parses_powershell(self) -> None:
        hook = _parse_hook_config({"type": "command", "command": "x", "shell": "powershell"})
        assert hook.shell == "powershell"

    def test_parses_bash(self) -> None:
        hook = _parse_hook_config({"type": "command", "command": "x", "shell": "bash"})
        assert hook.shell == "bash"

    def test_missing_shell_is_none(self) -> None:
        hook = _parse_hook_config({"type": "command", "command": "x"})
        assert hook.shell is None

    def test_unknown_shell_drops_to_none(self) -> None:
        # Parser stays permissive — validator catches it. The snapshot still
        # loads with the hook running on the default shell rather than
        # black-holing the whole config.
        hook = _parse_hook_config({"type": "command", "command": "x", "shell": "fish"})
        assert hook.shell is None

    def test_non_string_shell_drops_to_none(self) -> None:
        hook = _parse_hook_config({"type": "command", "command": "x", "shell": 42})
        assert hook.shell is None


class TestLoadHooksFromSettings:
    def test_roundtrip_powershell(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"type": "command", "command": "Write-Host hi", "shell": "powershell"}
                        ]
                    }
                },
                fh,
            )
            path = fh.name
        try:
            snapshot = load_hooks_from_settings(path)
            hooks = snapshot.hooks["PreToolUse"]
            assert len(hooks) == 1
            assert hooks[0].shell == "powershell"
            assert hooks[0].command == "Write-Host hi"
        finally:
            Path(path).unlink()


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TestValidatorShell:
    def test_bash_accepted(self) -> None:
        errors = validate_hook_configs(
            {"PreToolUse": [{"type": "command", "command": "x", "shell": "bash"}]}
        )
        assert errors == []

    def test_powershell_accepted(self) -> None:
        errors = validate_hook_configs(
            {"PreToolUse": [{"type": "command", "command": "x", "shell": "powershell"}]}
        )
        assert errors == []

    def test_unknown_shell_emits_error(self) -> None:
        errors = validate_hook_configs(
            {"PreToolUse": [{"type": "command", "command": "x", "shell": "fish"}]}
        )
        assert len(errors) == 1
        assert errors[0].field == "shell"
        assert errors[0].severity == "error"
        assert "fish" in errors[0].message
        assert "bash" in errors[0].message and "powershell" in errors[0].message

    def test_missing_shell_no_error(self) -> None:
        # Absent ``shell`` is fine — falls through to the default.
        errors = validate_hook_configs(
            {"PreToolUse": [{"type": "command", "command": "x"}]}
        )
        assert errors == []

    def test_shell_on_non_command_hook_is_ignored(self) -> None:
        # TS only puts ``shell`` on BashCommandHookSchema. Python mirrors that:
        # a stray ``shell`` on prompt / agent / http hooks isn't validated
        # (and isn't read by the parser into HookConfig.shell anyway).
        errors = validate_hook_configs(
            {
                "PreToolUse": [
                    {"type": "prompt", "promptText": "x", "shell": "anything"},
                    {"type": "http", "url": "https://example.com", "shell": "garbage"},
                    {"type": "agent", "agentInstructions": "x", "shell": "garbage"},
                ]
            }
        )
        # No ``shell`` errors. (Other validators may run, but this assertion
        # is scoped to the field we care about.)
        assert [e for e in errors if e.field == "shell"] == []


# ---------------------------------------------------------------------------
# _execute_command_hook — bash branch (default / explicit / unrecognized)
# ---------------------------------------------------------------------------


class _StubProcess:
    """Stand-in for ``asyncio.subprocess.Process`` with controllable stdout/stderr/code."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, _stdin: bytes | None = None):
        return self._stdout, self._stderr

    def kill(self):  # pragma: no cover - timeout path is not exercised here
        pass


class TestExecutorBashPath:
    @pytest.mark.asyncio
    async def test_default_uses_subprocess_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        async def fake_shell(cmd, *, stdin, stdout, stderr, env):
            captured["used"] = "shell"
            captured["cmd"] = cmd
            return _StubProcess(stdout=b"ok\n", returncode=0)

        async def fake_exec(*_args, **_kwargs):  # pragma: no cover - shouldn't be called
            captured["used"] = "exec"
            return _StubProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        hook = HookConfig(type="command", command="echo ok")  # shell=None
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})

        assert captured["used"] == "shell"
        assert captured["cmd"] == "echo ok"
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_explicit_bash_uses_subprocess_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        async def fake_shell(cmd, *, stdin, stdout, stderr, env):
            captured["used"] = "shell"
            return _StubProcess(returncode=0)

        async def fake_exec(*_args, **_kwargs):  # pragma: no cover
            captured["used"] = "exec"
            return _StubProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        hook = HookConfig(type="command", command="echo ok", shell="bash")
        await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert captured["used"] == "shell"


# ---------------------------------------------------------------------------
# _execute_command_hook — powershell branch
# ---------------------------------------------------------------------------


class TestExecutorPowerShellPath:
    @pytest.mark.asyncio
    async def test_spawns_pwsh_with_canonical_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        async def fake_exec(prog, *args, stdin, stdout, stderr, env):
            captured["prog"] = prog
            captured["args"] = list(args)
            return _StubProcess(stdout=b"", returncode=0)

        async def fake_shell(*_args, **_kwargs):  # pragma: no cover - shouldn't be called
            captured["fallback_called"] = True
            return _StubProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
        monkeypatch.setattr(
            "src.hooks.shell_invocation.shutil.which",
            lambda name: "/usr/local/bin/pwsh" if name == "pwsh" else None,
        )

        hook = HookConfig(type="command", command="Write-Host hi", shell="powershell")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})

        assert captured["prog"] == "/usr/local/bin/pwsh"
        assert captured["args"] == [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Write-Host hi",
        ]
        assert "fallback_called" not in captured
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_blocking_error_when_pwsh_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_exec(*_args, **_kwargs):  # pragma: no cover - shouldn't be called
            return _StubProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr("src.hooks.shell_invocation.shutil.which", lambda _name: None)

        hook = HookConfig(type="command", command="Write-Host hi", shell="powershell")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})

        assert result.blocking_error is not None
        assert "no PowerShell executable" in result.blocking_error
        assert "powershell" in result.blocking_error
        assert result.exit_code == -1
        assert result.command == "Write-Host hi"

    @pytest.mark.asyncio
    async def test_blocking_error_preserves_command_in_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.hooks.shell_invocation.shutil.which", lambda _name: None)
        hook = HookConfig(type="command", command="DoSomethingPS", shell="powershell")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert "DoSomethingPS" in (result.blocking_error or "")
