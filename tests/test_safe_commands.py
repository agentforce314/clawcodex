"""Tests for src/command_system/safe_commands.py.

Port of REMOTE_SAFE_COMMANDS / BRIDGE_SAFE_COMMANDS / isBridgeSafeCommand /
filterCommandsForRemoteMode from typescript/src/commands.ts:643-712.
"""

from __future__ import annotations

from src.command_system.safe_commands import (
    BRIDGE_SAFE_COMMANDS,
    REMOTE_SAFE_COMMANDS,
    filter_commands_for_remote_mode,
    is_bridge_safe_command,
)
from src.command_system.types import LocalCommand, PromptCommand


def test_remote_safe_set_size_and_contents():
    assert len(REMOTE_SAFE_COMMANDS) == 18
    # 'model' is intentionally excluded — the footgun the filter exists to stop.
    assert "model" not in REMOTE_SAFE_COMMANDS
    assert {"session", "exit", "clear", "help", "mobile"} <= REMOTE_SAFE_COMMANDS


def test_bridge_safe_set_size_and_contents():
    assert len(BRIDGE_SAFE_COMMANDS) == 6
    assert BRIDGE_SAFE_COMMANDS == {
        "compact", "clear", "cost", "summary", "release-notes", "files",
    }


def test_prompt_command_is_always_bridge_safe():
    # 'prompt' commands expand to text -> always safe, regardless of name.
    assert is_bridge_safe_command(PromptCommand(name="anything", description="d")) is True


def test_local_command_bridge_safe_only_if_allowlisted():
    assert is_bridge_safe_command(LocalCommand(name="compact", description="d")) is True
    assert is_bridge_safe_command(LocalCommand(name="help", description="d")) is False


def test_filter_commands_for_remote_mode_keeps_only_safe_names():
    clear = LocalCommand(name="clear", description="d")    # in REMOTE_SAFE
    help_ = LocalCommand(name="help", description="d")      # in REMOTE_SAFE
    doctor = LocalCommand(name="doctor", description="d")   # NOT in REMOTE_SAFE
    init = PromptCommand(name="init", description="d")      # NOT in REMOTE_SAFE
    result = filter_commands_for_remote_mode([clear, help_, doctor, init])
    assert {c.name for c in result} == {"clear", "help"}
    assert doctor not in result
    assert init not in result
