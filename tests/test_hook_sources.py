"""Phase-2 / WI-2.1 — per-source loader tests.

Covers the four file-backed source loaders + the plugin loader. Each test
sets ``CLAUDE_*_DIR`` / ``CLAUDE_PLUGINS_ROOT`` env vars to point at
``tmp_path`` so the loaders read fixtures rather than the user's real
configuration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.hooks.hook_types import HookConfig, HookSource
from src.hooks.sources.user_settings import (
    get_user_settings_path,
    load_user_hooks,
)
from src.hooks.sources.project_settings import (
    find_project_settings_path,
    load_project_hooks,
)
from src.hooks.sources.local_settings import (
    find_local_settings_path,
    load_local_hooks,
)
from src.hooks.sources.policy_settings import (
    get_policy_settings_path,
    load_policy_config,
    load_policy_hooks,
)
from src.utils.plugins.load_plugin_hooks import (
    get_plugins_root,
    load_plugin_hooks,
)


def _write_settings(path: Path, hooks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hooks}))


# ---------------------------------------------------------------------------
# User-tier
# ---------------------------------------------------------------------------


class TestUserSettingsLoader:
    def test_env_override_changes_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert get_user_settings_path() == tmp_path / "settings.json"

    def test_loader_returns_empty_when_file_missing(self, tmp_path):
        result = load_user_hooks(tmp_path / "nonexistent.json")
        assert result == {}

    def test_loader_returns_user_settings_tagged_hooks(self, tmp_path):
        path = tmp_path / "settings.json"
        _write_settings(path, {"PreToolUse": [{"type": "command", "command": "echo u"}]})
        hooks = load_user_hooks(path)
        assert "PreToolUse" in hooks
        assert hooks["PreToolUse"][0].source == HookSource.USER_SETTINGS

    def test_malformed_file_returns_empty(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("not valid json {")
        assert load_user_hooks(path) == {}


# ---------------------------------------------------------------------------
# Project-tier (walk up from workspace_root)
# ---------------------------------------------------------------------------


class TestProjectSettingsLoader:
    def test_walks_up_from_workspace_root(self, tmp_path):
        proj = tmp_path / "myproject"
        deep = proj / "src" / "deep" / "nested"
        deep.mkdir(parents=True)
        _write_settings(
            proj / ".claude" / "settings.json",
            {"PreToolUse": [{"type": "command", "command": "echo p"}]},
        )
        path = find_project_settings_path(deep)
        assert path == proj / ".claude" / "settings.json"

    def test_returns_none_when_no_dotclaude_dir(self, tmp_path):
        proj = tmp_path / "noproj"
        proj.mkdir()
        assert find_project_settings_path(proj) is None

    def test_loader_tags_with_project_settings(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _write_settings(
            proj / ".claude" / "settings.json",
            {"PreToolUse": [{"type": "command", "command": "echo p"}]},
        )
        hooks = load_project_hooks(proj)
        assert hooks["PreToolUse"][0].source == HookSource.PROJECT_SETTINGS

    def test_loader_returns_empty_when_workspace_none(self):
        assert load_project_hooks(None) == {}


# ---------------------------------------------------------------------------
# Local-tier (settings.local.json next to project settings)
# ---------------------------------------------------------------------------


class TestLocalSettingsLoader:
    def test_finds_local_alongside_project(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        _write_settings(proj / ".claude" / "settings.json", {})  # project anchor
        _write_settings(
            proj / ".claude" / "settings.local.json",
            {"PreToolUse": [{"type": "command", "command": "echo l"}]},
        )
        path = find_local_settings_path(proj)
        assert path == proj / ".claude" / "settings.local.json"

    def test_finds_local_even_without_project_settings(self, tmp_path):
        # Local-only setup (no settings.json, just settings.local.json).
        proj = tmp_path / "p"
        (proj / ".claude").mkdir(parents=True)
        _write_settings(
            proj / ".claude" / "settings.local.json",
            {"PreToolUse": [{"type": "command", "command": "echo l"}]},
        )
        path = find_local_settings_path(proj)
        assert path == proj / ".claude" / "settings.local.json"

    def test_loader_tags_with_local_settings(self, tmp_path):
        proj = tmp_path / "p"
        (proj / ".claude").mkdir(parents=True)
        _write_settings(
            proj / ".claude" / "settings.local.json",
            {"PreToolUse": [{"type": "command", "command": "echo l"}]},
        )
        hooks = load_local_hooks(proj)
        assert hooks["PreToolUse"][0].source == HookSource.LOCAL_SETTINGS


# ---------------------------------------------------------------------------
# Policy-tier
# ---------------------------------------------------------------------------


class TestPolicySettingsLoader:
    def test_env_override_changes_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(tmp_path))
        assert get_policy_settings_path() == tmp_path / "settings.json"

    def test_loader_tags_with_policy_settings(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(tmp_path))
        _write_settings(
            tmp_path / "settings.json",
            {"PreToolUse": [{"type": "command", "command": "echo policy"}]},
        )
        hooks = load_policy_hooks()
        assert hooks["PreToolUse"][0].source == HookSource.POLICY_SETTINGS

    def test_load_policy_config_returns_top_level(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(tmp_path))
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "disableAllHooks": True,
            "hooks": {"PreToolUse": [{"type": "command", "command": "x"}]},
        }))
        config = load_policy_config()
        assert config["disableAllHooks"] is True

    def test_load_policy_config_missing_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_POLICY_DIR", str(tmp_path))
        # No settings.json written.
        assert load_policy_config() == {}


# ---------------------------------------------------------------------------
# Plugin-tier
# ---------------------------------------------------------------------------


class TestPluginHookLoader:
    def test_env_override_changes_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path))
        assert get_plugins_root() == tmp_path

    def test_no_root_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path / "nonexistent"))
        assert load_plugin_hooks() == {}

    def test_plugin_with_hooks_json_loaded(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path))
        plugin = tmp_path / "my-plugin"
        plugin.mkdir()
        _write_settings(
            plugin / "hooks.json",
            {"PreToolUse": [{"type": "command", "command": "echo plug"}]},
        )
        hooks = load_plugin_hooks()
        assert "PreToolUse" in hooks
        assert hooks["PreToolUse"][0].source == HookSource.PLUGIN_HOOK

    def test_skill_root_set_to_plugin_dir(self, monkeypatch, tmp_path):
        # CLAUDE_PLUGIN_ROOT injection (WI-1.5) needs ``skill_root`` to
        # point at the plugin's directory.
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path))
        plugin = tmp_path / "my-plugin"
        plugin.mkdir()
        _write_settings(
            plugin / "hooks.json",
            {"PreToolUse": [{"type": "command", "command": "echo p"}]},
        )
        hooks = load_plugin_hooks()
        assert hooks["PreToolUse"][0].skill_root == str(plugin)

    def test_plugin_without_hooks_json_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path))
        plugin = tmp_path / "no-hooks-plugin"
        plugin.mkdir()
        # No hooks.json → silently skipped.
        assert load_plugin_hooks() == {}

    def test_multiple_plugins_merged(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGINS_ROOT", str(tmp_path))
        for name in ("plugin-a", "plugin-b"):
            p = tmp_path / name
            p.mkdir()
            _write_settings(
                p / "hooks.json",
                {"PreToolUse": [{"type": "command", "command": f"echo {name}"}]},
            )
        hooks = load_plugin_hooks()
        assert len(hooks["PreToolUse"]) == 2
