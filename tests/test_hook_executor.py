"""Tests for hook executor."""

from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path

from src.hooks.hook_executor import (
    HookConfig,
    _execute_command_hook,
    _matches_tool,
    has_hook_for_event,
)
from src.hooks.hook_types import TOOL_HOOK_EXECUTION_TIMEOUT_MS


class TestMatchesTool:
    def test_exact_match(self):
        assert _matches_tool("Bash", "Bash")
        assert not _matches_tool("Bash", "Read")

    def test_wildcard_suffix(self):
        assert _matches_tool("mcp__*", "mcp__server_tool")
        assert not _matches_tool("mcp__*", "Bash")

    def test_wildcard_prefix(self):
        assert _matches_tool("*Tool", "MyCustomTool")
        assert not _matches_tool("*Tool", "Bash")

    def test_none_matches_all(self):
        assert _matches_tool(None, "Bash")
        assert _matches_tool(None, "anything")


class TestHasHookForEvent:
    def test_no_hooks(self):
        class MockCtx:
            options = type("Options", (), {"hooks": None})()
        assert not has_hook_for_event("PreToolUse", MockCtx())

    def test_with_hooks(self):
        class MockCtx:
            options = type("Options", (), {
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo test"}]}
            })()
        assert has_hook_for_event("PreToolUse", MockCtx())

    def test_wrong_event(self):
        class MockCtx:
            options = type("Options", (), {
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo test"}]}
            })()
        assert not has_hook_for_event("PostToolUse", MockCtx())


class TestExecuteCommandHook:
    @pytest.mark.asyncio
    async def test_successful_hook(self):
        hook = HookConfig(type="command", command="echo 'hello'")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 0
        assert "hello" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_exit_code_2_blocking(self):
        hook = HookConfig(type="command", command="echo 'blocked' >&2; exit 2")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 2
        assert result.blocking_error is not None
        assert "blocked" in result.blocking_error

    @pytest.mark.asyncio
    async def test_non_zero_non_blocking(self):
        hook = HookConfig(type="command", command="exit 1")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 1
        assert result.blocking_error is None

    @pytest.mark.asyncio
    async def test_json_output_allow(self):
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "allow", "reason": "test"}\'',
        )
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code == 0
        assert result.permission_behavior == "allow"
        assert result.hook_permission_decision_reason == "test"

    @pytest.mark.asyncio
    async def test_json_output_deny(self):
        hook = HookConfig(
            type="command",
            command='echo \'{"decision": "deny", "reason": "not allowed"}\'',
        )
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.permission_behavior == "deny"

    @pytest.mark.asyncio
    async def test_json_output_updated_input(self):
        hook = HookConfig(
            type="command",
            command='echo \'{"updatedInput": {"command": "safer_cmd"}}\'',
        )
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.updated_input == {"command": "safer_cmd"}

    @pytest.mark.asyncio
    async def test_json_output_prevent_continuation(self):
        hook = HookConfig(
            type="command",
            command='echo \'{"preventContinuation": true, "stopReason": "done"}\'',
        )
        result = await _execute_command_hook(hook, {"hook_event": "Stop"})
        assert result.prevent_continuation is True
        assert result.stop_reason == "done"

    @pytest.mark.asyncio
    async def test_empty_command(self):
        hook = HookConfig(type="command", command="")
        result = await _execute_command_hook(hook, {"hook_event": "PreToolUse"})
        assert result.exit_code is None

    @pytest.mark.asyncio
    async def test_timeout(self):
        hook = HookConfig(type="command", command="sleep 10", timeout=100)
        result = await _execute_command_hook(
            hook, {"hook_event": "PreToolUse"}, timeout_ms=100
        )
        assert result.exit_code == -1
        assert result.blocking_error is not None
        assert "timed out" in result.blocking_error.lower()

    @pytest.mark.asyncio
    async def test_stdin_data_passed(self):
        hook = HookConfig(
            type="command",
            command="python3 -c 'import sys, json; d=json.load(sys.stdin); print(d[\"tool_name\"])'",
        )
        result = await _execute_command_hook(
            hook,
            {"hook_event": "PreToolUse", "tool_name": "Bash"},
        )
        assert result.exit_code == 0
        assert "Bash" in (result.stdout or "")
