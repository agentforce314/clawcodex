"""Phase-2 / WI-2.3 — policy cascade tests.

The chapter (``ch12-extensibility.md`` §"The Snapshot Security Model")
specifies two enterprise-managed flags that override the default merge
behavior:

  * ``disableAllHooks`` — clears EVERYTHING, including policy-source hooks
    (per §19 #8, critic-confirmed). Counterintuitive but matches TS; the
    rationale is "incident response — turn off all hooks, including audit."
  * ``allowManagedHooksOnly`` — keeps only policy-source hooks, drops
    user/project/local/plugin hooks.

The "policy wins" semantic critic asked for is exercised at the integration
level: a user setting that conflicts with policy is dropped when
``allowManagedHooksOnly`` is on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.hooks.config_manager import HookConfigManager
from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.policy import (
    apply_policy_cascade,
    should_allow_managed_hooks_only,
    should_disable_all_hooks,
)
from src.hooks.registry import AsyncHookRegistry


# ---------------------------------------------------------------------------
# Pure-function predicate tests
# ---------------------------------------------------------------------------


class TestPolicyPredicates:
    def test_disable_all_hooks_default_false(self):
        assert should_disable_all_hooks({}) is False

    def test_disable_all_hooks_explicit_true(self):
        assert should_disable_all_hooks({"disableAllHooks": True}) is True

    def test_disable_all_hooks_explicit_false(self):
        assert should_disable_all_hooks({"disableAllHooks": False}) is False

    def test_disable_all_hooks_truthy_coercion(self):
        # bool() coercion: any truthy value enables the flag, matching
        # TS' permissive ``Boolean(...)`` cast.
        assert should_disable_all_hooks({"disableAllHooks": 1}) is True
        assert should_disable_all_hooks({"disableAllHooks": "yes"}) is True

    def test_allow_managed_only_default_false(self):
        assert should_allow_managed_hooks_only({}) is False

    def test_allow_managed_only_explicit(self):
        assert should_allow_managed_hooks_only({"allowManagedHooksOnly": True}) is True


# ---------------------------------------------------------------------------
# apply_policy_cascade — pure transformation tests
# ---------------------------------------------------------------------------


def _hook(cmd: str, source: HookSource) -> HookConfig:
    return HookConfig(type="command", command=cmd, source=source)


class TestApplyPolicyCascade:
    def test_disable_all_clears_everything_including_policy(self):
        # Critic-confirmed §19 #8: ``disableAllHooks`` clears policy too.
        merged = {
            "PreToolUse": [
                _hook("u", HookSource.USER_SETTINGS),
                _hook("policy", HookSource.POLICY_SETTINGS),
            ],
        }
        result = apply_policy_cascade(merged, {"disableAllHooks": True})
        assert result == {}

    def test_allow_managed_only_keeps_policy_drops_others(self):
        merged = {
            "PreToolUse": [
                _hook("user", HookSource.USER_SETTINGS),
                _hook("project", HookSource.PROJECT_SETTINGS),
                _hook("local", HookSource.LOCAL_SETTINGS),
                _hook("policy", HookSource.POLICY_SETTINGS),
                _hook("plugin", HookSource.PLUGIN_HOOK),
            ],
        }
        result = apply_policy_cascade(merged, {"allowManagedHooksOnly": True})
        assert "PreToolUse" in result
        assert len(result["PreToolUse"]) == 1
        assert result["PreToolUse"][0].source == HookSource.POLICY_SETTINGS

    def test_allow_managed_only_drops_event_with_no_policy_hook(self):
        # If an event has no policy-source hooks, it disappears from the
        # result entirely (tidier snapshot).
        merged = {
            "PreToolUse": [_hook("u", HookSource.USER_SETTINGS)],
            "PostToolUse": [_hook("policy", HookSource.POLICY_SETTINGS)],
        }
        result = apply_policy_cascade(merged, {"allowManagedHooksOnly": True})
        assert "PreToolUse" not in result
        assert "PostToolUse" in result

    def test_no_flags_returns_unchanged(self):
        merged = {"PreToolUse": [_hook("a", HookSource.USER_SETTINGS)]}
        result = apply_policy_cascade(merged, {})
        assert result == merged
        # Returns a NEW dict (no mutation contract).
        assert result is not merged

    def test_input_not_mutated(self):
        merged = {
            "PreToolUse": [
                _hook("u", HookSource.USER_SETTINGS),
                _hook("policy", HookSource.POLICY_SETTINGS),
            ],
        }
        before = {ev: list(hs) for ev, hs in merged.items()}
        _ = apply_policy_cascade(merged, {"allowManagedHooksOnly": True})
        # Original dict untouched.
        assert merged == before


# ---------------------------------------------------------------------------
# Integration: HookConfigManager.load() with policy cascade end-to-end
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class TestPolicyCascadeIntegration:
    """End-to-end: load user + policy + project + local hooks, apply
    cascade, observe the snapshot.
    """

    @pytest.mark.asyncio
    async def test_disable_all_clears_user_and_policy(self, monkeypatch, tmp_path):
        # Set up two source files: user settings with one hook, policy
        # settings with disableAllHooks=true plus its own hook.
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        _write_json(user_dir / "settings.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo u"}]},
        })
        _write_json(policy_dir / "settings.json", {
            "disableAllHooks": True,
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo p"}]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
        )
        snapshot = await manager.load()
        # Everything cleared, including the policy hook.
        assert snapshot.hooks == {}

    @pytest.mark.asyncio
    async def test_allow_managed_only_drops_user_keeps_policy(self, monkeypatch, tmp_path):
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        _write_json(user_dir / "settings.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo user"}]},
        })
        _write_json(policy_dir / "settings.json", {
            "allowManagedHooksOnly": True,
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo policy"}]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
        )
        snapshot = await manager.load()
        # User hook dropped; policy hook survives.
        assert "PreToolUse" in snapshot.hooks
        assert len(snapshot.hooks["PreToolUse"]) == 1
        assert snapshot.hooks["PreToolUse"][0].source == HookSource.POLICY_SETTINGS
        assert snapshot.hooks["PreToolUse"][0].command == "echo policy"

    @pytest.mark.asyncio
    async def test_policy_cascade_overrides_user_settings(self, monkeypatch, tmp_path):
        # Critic ask: a test that exercises the "policy wins" semantic.
        # Setup: user defines a hook for PreToolUse; policy says
        # ``allowManagedHooksOnly: true`` AND defines a different hook.
        # Expected: only the policy hook survives.
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        _write_json(user_dir / "settings.json", {
            "hooks": {"PreToolUse": [
                {"type": "command", "command": "user-says-allow",
                 "matcher": "Bash"},
            ]},
        })
        _write_json(policy_dir / "settings.json", {
            "allowManagedHooksOnly": True,
            "hooks": {"PreToolUse": [
                {"type": "command", "command": "policy-says-deny",
                 "matcher": "Bash"},
            ]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
        )
        snapshot = await manager.load()
        # User's ``user-says-allow`` is dropped by the cascade; the policy
        # hook is the only surviving entry, and it's tagged POLICY_SETTINGS.
        assert "PreToolUse" in snapshot.hooks
        survivors = snapshot.hooks["PreToolUse"]
        assert len(survivors) == 1
        assert survivors[0].command == "policy-says-deny"
        assert survivors[0].source == HookSource.POLICY_SETTINGS

    @pytest.mark.asyncio
    async def test_no_policy_flags_keeps_all_sources(self, monkeypatch, tmp_path):
        # No ``disableAllHooks`` / ``allowManagedHooksOnly`` flags →
        # cascade is a no-op, all sources flow through to the snapshot.
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        _write_json(user_dir / "settings.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "user"}]},
        })
        _write_json(policy_dir / "settings.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "policy"}]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
        )
        snapshot = await manager.load()
        assert "PreToolUse" in snapshot.hooks
        # Both sources represented.
        commands = {h.command for h in snapshot.hooks["PreToolUse"]}
        sources = {h.source for h in snapshot.hooks["PreToolUse"]}
        assert "user" in commands
        assert "policy" in commands
        assert HookSource.USER_SETTINGS in sources
        assert HookSource.POLICY_SETTINGS in sources

    @pytest.mark.asyncio
    async def test_workspace_root_picks_up_project_and_local(self, monkeypatch, tmp_path):
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        _write_json(workspace / ".claude" / "settings.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "project"}]},
        })
        _write_json(workspace / ".claude" / "settings.local.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "local"}]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
            workspace_root=workspace,
        )
        snapshot = await manager.load()
        commands = {h.command for h in snapshot.hooks["PreToolUse"]}
        sources = {h.source for h in snapshot.hooks["PreToolUse"]}
        assert "project" in commands
        assert "local" in commands
        assert HookSource.PROJECT_SETTINGS in sources
        assert HookSource.LOCAL_SETTINGS in sources

    @pytest.mark.asyncio
    async def test_plugin_hooks_loaded_with_skill_root(self, monkeypatch, tmp_path):
        user_dir = tmp_path / "user"
        policy_dir = tmp_path / "policy"
        plugins_dir = tmp_path / "plugins"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(policy_dir))
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(plugins_dir))

        plugin_dir = plugins_dir / "my-plugin"
        plugin_dir.mkdir(parents=True)
        _write_json(plugin_dir / "hooks.json", {
            "hooks": {"PreToolUse": [{"type": "command", "command": "plug"}]},
        })

        manager = HookConfigManager(
            registry=AsyncHookRegistry(),
            settings_path=user_dir / "settings.json",
        )
        snapshot = await manager.load()
        assert "PreToolUse" in snapshot.hooks
        plugin_hooks = [h for h in snapshot.hooks["PreToolUse"]
                        if h.source == HookSource.PLUGIN_HOOK]
        assert len(plugin_hooks) == 1
        # ``skill_root`` populated for CLAUDE_PLUGIN_ROOT env injection.
        assert plugin_hooks[0].skill_root == str(plugin_dir)
