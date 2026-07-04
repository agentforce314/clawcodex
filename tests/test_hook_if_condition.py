"""SCHEMAS-1 — the hook `if` permission-rule pre-filter.

The field (HookConfig.if_condition) round-tripped through config but was
NEVER evaluated (_run_hooks_for_event filtered only by matcher) — a hook
with `if: "Bash(git *)"` ran for every Bash command. Port of
prepareIfConditionMatcher (utils/hooks.ts:1571-1610). Execution-style per
the plugins/query lessons: real command hooks + marker files prove the
hook actually does/doesn't spawn.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.hooks.hook_executor import _matches_if_condition, _run_hooks_for_event
from src.hooks.hook_types import HookConfig, HookSource


class _Snapshot:
    def __init__(self, hooks):
        self.hooks = hooks


def _ctx(configs):
    from src.tool_system.context import ToolContext

    ctx = ToolContext(workspace_root=Path("/tmp"))
    ctx.workspace_trusted = True
    mgr = MagicMock()
    mgr.snapshot = _Snapshot({"PreToolUse": configs})
    ctx.hook_config_manager = mgr
    return ctx


def _run(ctx, tool_name, tool_input):
    async def go():
        out = []
        async for r in _run_hooks_for_event(
            "PreToolUse", tool_name,
            {"tool_name": tool_name, "tool_input": tool_input, "tool_use_id": "t"},
            ctx,
        ):
            out.append(r)
        return out

    return asyncio.run(go())


class TestMatcherUnit:
    def test_matching_command_runs(self):
        assert _matches_if_condition("Bash(git *)", "PreToolUse", "Bash", {"command": "git status"}) is True

    def test_non_matching_command_skips(self):
        assert _matches_if_condition("Bash(git *)", "PreToolUse", "Bash", {"command": "ls"}) is False

    def test_tool_mismatch_skips(self):
        assert _matches_if_condition("Bash(git *)", "PreToolUse", "Read", {"file_path": "x"}) is False

    def test_no_rule_content_runs(self):
        assert _matches_if_condition("Bash", "PreToolUse", "Bash", {"command": "anything"}) is True

    def test_non_tool_event_with_if_skips(self):
        # TS parity (hooks.ts:2023-2027): a tool-syntax `if` cannot be
        # evaluated for a non-tool event → SKIP (not ignore-and-run).
        assert _matches_if_condition("Bash(git *)", "Stop", None, None) is False

    def test_no_condition_runs(self):
        assert _matches_if_condition(None, "PreToolUse", "Bash", {"command": "x"}) is True

    def test_file_tool_if_evaluated(self):
        # MAJOR-1 fix: Read/Edit/Write/Glob/Grep are evaluated (were
        # fail-closed). The `if` schema's own example is Read(*.ts).
        assert _matches_if_condition("Read(*.ts)", "PreToolUse", "Read", {"file_path": "a.ts"}) is True
        assert _matches_if_condition("Read(*.ts)", "PreToolUse", "Read", {"file_path": "a.py"}) is False

    def test_edit_env_guard_fires(self):
        # The safety case: an `if:"Edit(*.env)"` PreToolUse guard must FIRE
        # on a .env write (pre-fix it silently never did).
        assert _matches_if_condition("Edit(*.env)", "PreToolUse", "Edit", {"file_path": ".env"}) is True

    def test_bash_chaining_any_subcommand(self):
        # MINOR-2 fix: `Bash(git *)` fires on a chained command if ANY
        # sub-command matches (matchWildcardPattern per sub-command, not the
        # chaining-strict permission matcher).
        assert _matches_if_condition("Bash(git *)", "PreToolUse", "Bash", {"command": "git push && npm test"}) is True

    def test_unsupported_tool_fails_open(self):
        # A tool with no matcher analog runs the hook (fail-OPEN + warn) —
        # never silently disabled.
        assert _matches_if_condition("CustomTool(x)", "PreToolUse", "CustomTool", {"y": 1}) is True


class TestExecutionEnforcement:
    """The gap this closes: the hook must actually NOT spawn when `if`
    excludes the command, and spawn when it matches."""

    def _hook(self, command, if_condition):
        return [HookConfig(
            type="command", command=command, if_condition=if_condition,
            source=HookSource.PROJECT_SETTINGS,
        )]

    def test_if_excludes_nonmatching_command(self, tmp_path):
        marker = tmp_path / "ran"
        ctx = _ctx(self._hook(f"touch {marker}", "Bash(git *)"))
        _run(ctx, "Bash", {"command": "ls -la"})
        assert not marker.exists(), "the `if` filter must skip a non-git command"

    def test_if_allows_matching_command(self, tmp_path):
        marker = tmp_path / "ran"
        ctx = _ctx(self._hook(f"touch {marker}", "Bash(git *)"))
        _run(ctx, "Bash", {"command": "git commit -m x"})
        assert marker.exists(), "the `if` filter must run for a matching command"

    def test_no_if_still_runs(self, tmp_path):
        marker = tmp_path / "ran"
        ctx = _ctx(self._hook(f"touch {marker}", None))
        _run(ctx, "Bash", {"command": "anything"})
        assert marker.exists(), "no `if` → unconditional (unchanged behavior)"

    def test_file_if_excludes_nonmatching_path(self, tmp_path):
        marker = tmp_path / "ran"
        ctx = _ctx([HookConfig(
            type="command", command=f"touch {marker}", if_condition="Read(*.ts)",
            source=HookSource.PROJECT_SETTINGS,
        )])
        _run(ctx, "Read", {"file_path": "notes.md"})
        assert not marker.exists(), "Read(*.ts) must skip a .md read"

    def test_file_if_allows_matching_path(self, tmp_path):
        marker = tmp_path / "ran"
        ctx = _ctx([HookConfig(
            type="command", command=f"touch {marker}", if_condition="Read(*.ts)",
            source=HookSource.PROJECT_SETTINGS,
        )])
        _run(ctx, "Read", {"file_path": "app.ts"})
        assert marker.exists(), "Read(*.ts) must run on a .ts read"
