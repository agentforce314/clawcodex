"""Phase-1 / WI-1.5 — env-var injection at hook fire time.

Three new env vars on top of inherited ``os.environ``:
  * ``CLAUDE_PROJECT_DIR`` — workspace root from active context.
  * ``CLAUDE_PLUGIN_ROOT`` — set from ``hook.skill_root`` (skill-declared
    hooks only).
  * ``CLAUDE_ENV_FILE`` — per-fire ephemeral path. Set ONLY for
    ``SessionStart``, ``Setup``, ``CwdChanged``. Per N4: this WI sets the
    path; sourcing-and-applying loop is a separate follow-up ticket.

These tests cover ``_build_hook_env`` directly (unit-level) plus a
subprocess round-trip that verifies the env var is actually visible to the
hook command.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from src.hooks.hook_executor import _build_hook_env, _execute_command_hook
from src.hooks.hook_types import HookConfig


@dataclass
class _MockCtx:
    workspace_root: str = "/some/workspace"


class TestBuildHookEnv:
    def test_claude_project_dir_set_from_workspace_root(self):
        ctx = _MockCtx(workspace_root="/work/dir")
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PROJECT_DIR"] == "/work/dir"

    def test_claude_project_dir_empty_when_no_context(self):
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, None)
        assert env["CLAUDE_PROJECT_DIR"] == ""

    def test_claude_plugin_root_from_skill_root(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x", skill_root="/skills/my-skill")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PLUGIN_ROOT"] == "/skills/my-skill"

    def test_claude_plugin_root_empty_for_non_skill_hooks(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")  # skill_root=None
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_PLUGIN_ROOT"] == ""

    def test_claude_env_file_set_for_session_start(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "SessionStart"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""
        # Path is under ~/.clawcodex/hook-env/ — fail-loud if the layout
        # changes silently.
        assert "hook-env" in env["CLAUDE_ENV_FILE"]
        assert "SessionStart" in env["CLAUDE_ENV_FILE"]

    def test_claude_env_file_set_for_setup(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "Setup"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""

    def test_claude_env_file_set_for_cwd_changed(self):
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "CwdChanged"}, ctx)
        assert env["CLAUDE_ENV_FILE"] != ""

    def test_claude_env_file_empty_for_pre_tool_use(self):
        # PreToolUse is a tool-lifecycle event, not a lifecycle env-propagation
        # event.
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
        assert env["CLAUDE_ENV_FILE"] == ""

    def test_claude_hook_event_preserved(self):
        # Pre-existing var stays.
        ctx = _MockCtx()
        hook = HookConfig(type="command", command="x")
        env = _build_hook_env(hook, {"hook_event": "PostToolUse"}, ctx)
        assert env["CLAUDE_HOOK_EVENT"] == "PostToolUse"

    def test_inherited_environment_preserved(self):
        # The new vars don't clobber inherited environment.
        os.environ["CLAW_TEST_PRESERVED"] = "yes"
        try:
            ctx = _MockCtx()
            hook = HookConfig(type="command", command="x")
            env = _build_hook_env(hook, {"hook_event": "PreToolUse"}, ctx)
            assert env.get("CLAW_TEST_PRESERVED") == "yes"
        finally:
            del os.environ["CLAW_TEST_PRESERVED"]


class TestEnvVisibleToSubprocess:
    @pytest.mark.asyncio
    async def test_command_sees_claude_project_dir(self):
        ctx = _MockCtx(workspace_root="/expected/path")
        hook = HookConfig(
            type="command",
            command='printf "DIR=%s" "$CLAUDE_PROJECT_DIR"',
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "DIR=/expected/path" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_command_sees_claude_plugin_root(self):
        ctx = _MockCtx()
        hook = HookConfig(
            type="command",
            command='printf "ROOT=%s" "$CLAUDE_PLUGIN_ROOT"',
            skill_root="/path/to/skill",
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "PreToolUse"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "ROOT=/path/to/skill" in (result.stdout or "")

    @pytest.mark.asyncio
    async def test_command_sees_claude_env_file_for_session_start(self):
        ctx = _MockCtx()
        hook = HookConfig(
            type="command",
            command='printf "FILE=%s" "$CLAUDE_ENV_FILE"',
        )
        result = await _execute_command_hook(
            hook, {"hook_event": "SessionStart"}, tool_use_context=ctx,
        )
        assert result.exit_code == 0
        assert "hook-env" in (result.stdout or "")
        assert "SessionStart" in (result.stdout or "")
