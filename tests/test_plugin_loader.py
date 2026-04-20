import json
import tempfile
from pathlib import Path

import pytest

from src.plugins.loader import (
    clear_loaded_plugins,
    discover_plugins,
    get_enabled_plugins,
    get_loaded_plugin,
    get_loaded_plugins,
    load_plugin_from_directory,
    load_plugins_from_directories,
    register_plugin,
    unregister_plugin,
)
from src.plugins.types import LoadedPlugin, PluginError, PluginManifest


@pytest.fixture(autouse=True)
def _clean():
    clear_loaded_plugins()
    yield
    clear_loaded_plugins()


def _make_plugin_dir(base: Path, name: str, **extra) -> Path:
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "description": f"Plugin {name}", "version": "1.0.0"}
    manifest.update(extra)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
    return plugin_dir


class TestLoadPluginFromDirectory:
    def test_load_valid(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "test-plugin")
        plugin = load_plugin_from_directory(plugin_dir)
        assert plugin.name == "test-plugin"
        assert plugin.manifest.version == "1.0.0"
        assert plugin.enabled is True

    def test_load_with_hooks(self, tmp_path):
        plugin_dir = _make_plugin_dir(
            tmp_path, "hooked", hooks={"PreToolUse": [{"command": "echo"}]}
        )
        plugin = load_plugin_from_directory(plugin_dir)
        assert plugin.hooks_config is not None
        assert "PreToolUse" in plugin.hooks_config

    def test_load_with_mcp(self, tmp_path):
        plugin_dir = _make_plugin_dir(
            tmp_path, "mcp-plugin", mcp_servers={"server1": {"command": "node"}}
        )
        plugin = load_plugin_from_directory(plugin_dir)
        assert plugin.mcp_servers is not None
        assert "server1" in plugin.mcp_servers

    def test_load_disabled(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "disabled-plugin", enabled=False)
        plugin = load_plugin_from_directory(plugin_dir)
        assert plugin.enabled is False

    def test_missing_manifest(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(PluginError, match="No plugin.json"):
            load_plugin_from_directory(empty_dir)

    def test_invalid_json(self, tmp_path):
        plugin_dir = tmp_path / "bad-json"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text("not json")
        with pytest.raises(PluginError, match="Failed to read"):
            load_plugin_from_directory(plugin_dir)

    def test_invalid_manifest(self, tmp_path):
        plugin_dir = tmp_path / "bad-manifest"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"version": "1.0.0"}))
        with pytest.raises(PluginError, match="Invalid manifest"):
            load_plugin_from_directory(plugin_dir)

    def test_custom_source(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, "sourced")
        plugin = load_plugin_from_directory(plugin_dir, source="managed")
        assert plugin.source == "managed"


class TestDiscoverPlugins:
    def test_discover_multiple(self, tmp_path):
        _make_plugin_dir(tmp_path, "plugin-a")
        _make_plugin_dir(tmp_path, "plugin-b")
        result = discover_plugins(tmp_path)
        assert len(result.plugins) == 2
        names = {p.name for p in result.plugins}
        assert names == {"plugin-a", "plugin-b"}

    def test_discover_nonexistent(self, tmp_path):
        result = discover_plugins(tmp_path / "no-such-dir")
        assert result.plugins == []
        assert result.errors == []

    def test_discover_skips_files(self, tmp_path):
        _make_plugin_dir(tmp_path, "valid-plugin")
        (tmp_path / "not-a-dir.txt").write_text("hello")
        result = discover_plugins(tmp_path)
        assert len(result.plugins) == 1

    def test_discover_collects_errors(self, tmp_path):
        _make_plugin_dir(tmp_path, "good-plugin")
        bad_dir = tmp_path / "bad-plugin"
        bad_dir.mkdir()
        (bad_dir / "plugin.json").write_text("broken")
        result = discover_plugins(tmp_path)
        assert len(result.plugins) == 1
        assert len(result.errors) == 1

    def test_discover_empty(self, tmp_path):
        result = discover_plugins(tmp_path)
        assert result.plugins == []


class TestPluginRegistry:
    def test_register_and_get(self):
        plugin = LoadedPlugin(
            name="reg-test",
            manifest=PluginManifest(name="reg-test"),
            enabled=True,
        )
        register_plugin(plugin)
        assert get_loaded_plugin("reg-test") is not None

    def test_get_nonexistent(self):
        assert get_loaded_plugin("nope") is None

    def test_unregister(self):
        plugin = LoadedPlugin(
            name="temp",
            manifest=PluginManifest(name="temp"),
        )
        register_plugin(plugin)
        assert unregister_plugin("temp") is True
        assert get_loaded_plugin("temp") is None

    def test_unregister_nonexistent(self):
        assert unregister_plugin("nope") is False

    def test_get_all(self):
        for i in range(3):
            register_plugin(LoadedPlugin(
                name=f"plugin-{i}",
                manifest=PluginManifest(name=f"plugin-{i}"),
            ))
        assert len(get_loaded_plugins()) == 3

    def test_get_enabled(self):
        register_plugin(LoadedPlugin(
            name="enabled",
            manifest=PluginManifest(name="enabled"),
            enabled=True,
        ))
        register_plugin(LoadedPlugin(
            name="disabled",
            manifest=PluginManifest(name="disabled"),
            enabled=False,
        ))
        enabled = get_enabled_plugins()
        assert len(enabled) == 1
        assert enabled[0].name == "enabled"

    def test_clear(self):
        register_plugin(LoadedPlugin(
            name="temp",
            manifest=PluginManifest(name="temp"),
        ))
        clear_loaded_plugins()
        assert get_loaded_plugins() == []


class TestLoadPluginsFromDirectories:
    def test_load_from_multiple(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        _make_plugin_dir(dir1, "plugin-a")
        _make_plugin_dir(dir2, "plugin-b")
        result = load_plugins_from_directories([dir1, dir2])
        assert len(result.plugins) == 2
        assert len(get_loaded_plugins()) == 2
