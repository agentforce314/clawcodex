"""Phase-4 acceptance gate — chapter example #1 end-to-end.

The chapter (``ch12-extensibility.md``) describes a settings.json hook
configured with ``"if": "Bash(git commit*)"`` that fires only on Bash
``git commit`` calls. Pre-Phase-4 this was inert: the matcher had no way
to express "only this command pattern," so the hook fired on every Bash
call.

This E2E test pins the full chain:
  1. Settings.json with ``hooks.PreToolUse[].if: "Bash(git commit*)"``
     loads correctly via ``load_hooks_from_settings`` (Phase 1's
     parser populates ``HookConfig.if_condition``).
  2. ``HookConfigManager.load()`` builds a snapshot from the settings
     (Phase 2's multi-source loader).
  3. ``_run_hooks_for_event`` for ``PreToolUse + Bash + git commit -m "..."``
     matches via ``matches_hook_condition`` (Phase 4's WI-4.2) and the
     hook fires.
  4. ``_run_hooks_for_event`` for ``PreToolUse + Bash + ls`` does NOT match
     and the hook stays silent.

This is the guard for the chapter's "block commits to main" example.
A regression here means the chapter's headline use case is broken.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.hooks.config_manager import HookConfigManager
from src.hooks.hook_executor import _run_hooks_for_event
from src.hooks.registry import AsyncHookRegistry


@dataclass
class _MockOptions:
    hooks: dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)


@dataclass
class _MockContext:
    options: _MockOptions = field(default_factory=_MockOptions)
    hook_config_manager: Any | None = None
    workspace_trusted: bool = True
    abort_controller: Any | None = None
    session_hook_registry: Any | None = None
    session_id: str | None = None
    workspace_root: Path | None = None


async def _build_manager_from_settings_json(
    settings_path: Path,
) -> HookConfigManager:
    manager = HookConfigManager(
        registry=AsyncHookRegistry(),
        settings_path=settings_path,
    )
    await manager.load()
    return manager


def _collect_fire_signals(yields: list[dict[str, Any]]) -> list[str]:
    """Extract command strings from progress messages so we can prove
    a hook fired (or didn't).
    """
    signals: list[str] = []
    for item in yields:
        msg = item.get("message")
        data = getattr(msg, "data", None) or {}
        if isinstance(data, dict):
            cmd = data.get("command")
            if isinstance(cmd, str) and cmd:
                signals.append(cmd)
    return signals


class TestIfConditionedCommitGuardE2E:
    @pytest.mark.asyncio
    async def test_if_conditioned_commit_guard_e2e(self, tmp_path, monkeypatch):
        # 1. Settings.json with the chapter's worked example #1 shape.
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        settings_path = user_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "type": "command",
                    "command": "echo 'blocked-commit-to-main'",
                    "if": "Bash(git commit*)",
                }],
            },
        }))
        # Isolate from the real user's home so other source loaders
        # (project/local/policy/plugin) don't pull in side data.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))

        manager = await _build_manager_from_settings_json(settings_path)
        # Sanity: snapshot has the hook with ``if_condition`` populated.
        assert "PreToolUse" in manager.snapshot.hooks
        loaded = manager.snapshot.hooks["PreToolUse"][0]
        assert loaded.if_condition == "Bash(git commit*)"
        assert loaded.command == "echo 'blocked-commit-to-main'"

        ctx = _MockContext(hook_config_manager=manager)

        # 2. Bash + git commit → hook fires.
        commit_yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix'"}},
            ctx,
        ):
            commit_yields.append(item)

        commit_signals = _collect_fire_signals(commit_yields)
        assert any("blocked-commit-to-main" in s for s in commit_signals), (
            f"hook did not fire on `git commit`; yields={commit_signals!r}"
        )

        # 3. Bash + ls → hook does NOT fire.
        ls_yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
            ctx,
        ):
            ls_yields.append(item)

        ls_signals = _collect_fire_signals(ls_yields)
        assert not any("blocked-commit-to-main" in s for s in ls_signals), (
            f"hook over-fired on `ls`; this is the regression that "
            f"chapter example #1 was inert before Phase 4: {ls_signals!r}"
        )

    @pytest.mark.asyncio
    async def test_if_conditioned_commit_guard_blocks_with_exit_2(
        self, tmp_path, monkeypatch,
    ):
        """Variant: hook EXITS 2 to actually block the tool call.

        Pins the chapter's "block commits to main" semantic — the hook
        produces a ``blocking_error`` payload so downstream consumers
        (the agent loop) reject the tool call.
        """
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        settings_path = user_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "type": "command",
                    "command": "echo 'cannot-commit-to-main' >&2; exit 2",
                    "if": "Bash(git commit*)",
                }],
            },
        }))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))

        manager = await _build_manager_from_settings_json(settings_path)
        ctx = _MockContext(hook_config_manager=manager)

        commit_yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            ctx,
        ):
            commit_yields.append(item)

        # The aggregated ``blocking_error`` yield is what downstream
        # consumers act on. Pre-Phase-4 each hook yielded its own
        # blocking_error; post-Phase-4 the aggregator yields exactly one.
        blocking_yields = [
            item for item in commit_yields if "blocking_error" in item
        ]
        assert len(blocking_yields) == 1
        assert "cannot-commit-to-main" in str(blocking_yields[0])

    @pytest.mark.asyncio
    async def test_if_condition_with_no_match_does_not_block(
        self, tmp_path, monkeypatch,
    ):
        """Counterpart: ls falls outside the if_condition → no
        ``blocking_error`` aggregated, no decision yields at all.
        """
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        settings_path = user_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "type": "command",
                    "command": "echo 'block' >&2; exit 2",
                    "if": "Bash(git commit*)",
                }],
            },
        }))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))

        manager = await _build_manager_from_settings_json(settings_path)
        ctx = _MockContext(hook_config_manager=manager)

        ls_yields: list[dict[str, Any]] = []
        async for item in _run_hooks_for_event(
            "PreToolUse", "Bash",
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            ctx,
        ):
            ls_yields.append(item)

        blocking_yields = [
            item for item in ls_yields if "blocking_error" in item
        ]
        assert blocking_yields == [], (
            f"hook spuriously blocked `ls`: {blocking_yields!r}"
        )
