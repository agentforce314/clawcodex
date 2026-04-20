import json
import tempfile
from pathlib import Path

import pytest

from src.plugins.loader import clear_loaded_plugins
from src.plugins.marketplace import (
    MarketplaceEntry,
    clear_marketplace_index,
    install_plugin,
    list_marketplace,
    load_marketplace_index,
    search_marketplace,
    uninstall_plugin,
)
from src.plugins.types import PluginError


@pytest.fixture(autouse=True)
def _clean():
    clear_marketplace_index()
    clear_loaded_plugins()
    yield
    clear_marketplace_index()
    clear_loaded_plugins()


def _make_index(path: Path, plugins: list[dict]) -> None:
    data = {"plugins": plugins, "last_updated": "2025-01-01"}
    path.write_text(json.dumps(data))


def _make_plugin_source(base: Path, name: str) -> Path:
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "description": f"Plugin {name}", "version": "1.0.0"}
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
    return plugin_dir


class TestLoadMarketplaceIndex:
    def test_load(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "plugin-a", "description": "A", "version": "1.0.0", "downloads": 100},
            {"name": "plugin-b", "description": "B", "version": "2.0.0", "tags": ["test"]},
        ])
        idx = load_marketplace_index(index_file)
        assert len(idx.entries) == 2
        assert idx.entries[0].name == "plugin-a"
        assert idx.entries[0].downloads == 100
        assert idx.last_updated == "2025-01-01"

    def test_load_nonexistent(self, tmp_path):
        idx = load_marketplace_index(tmp_path / "nope.json")
        assert idx.entries == []

    def test_load_invalid_json(self, tmp_path):
        index_file = tmp_path / "bad.json"
        index_file.write_text("not json")
        idx = load_marketplace_index(index_file)
        assert idx.entries == []


class TestSearchMarketplace:
    def test_search_by_name(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "formatter", "description": "Code formatter"},
            {"name": "linter", "description": "Code linter"},
        ])
        load_marketplace_index(index_file)
        results = search_marketplace("format")
        assert len(results) == 1
        assert results[0].name == "formatter"

    def test_search_by_description(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "plugin-a", "description": "A Python tool"},
            {"name": "plugin-b", "description": "A Rust tool"},
        ])
        load_marketplace_index(index_file)
        results = search_marketplace("python")
        assert len(results) == 1

    def test_search_with_tags(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "a", "description": "tool", "tags": ["python"]},
            {"name": "b", "description": "tool", "tags": ["rust"]},
        ])
        load_marketplace_index(index_file)
        results = search_marketplace("tool", tags=["python"])
        assert len(results) == 1
        assert results[0].name == "a"

    def test_search_no_index(self):
        results = search_marketplace("anything")
        assert results == []

    def test_search_case_insensitive(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "MyPlugin", "description": "Something"},
        ])
        load_marketplace_index(index_file)
        results = search_marketplace("myplugin")
        assert len(results) == 1


class TestListMarketplace:
    def test_list_sorted_by_name(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "zzz", "description": "last"},
            {"name": "aaa", "description": "first"},
        ])
        load_marketplace_index(index_file)
        entries = list_marketplace()
        assert entries[0].name == "aaa"

    def test_list_sorted_by_downloads(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": "low", "description": "low", "downloads": 10},
            {"name": "high", "description": "high", "downloads": 1000},
        ])
        load_marketplace_index(index_file)
        entries = list_marketplace(sort_by="downloads")
        assert entries[0].name == "high"

    def test_list_limit(self, tmp_path):
        index_file = tmp_path / "index.json"
        _make_index(index_file, [
            {"name": f"p{i}", "description": f"d{i}"} for i in range(10)
        ])
        load_marketplace_index(index_file)
        entries = list_marketplace(limit=3)
        assert len(entries) == 3

    def test_list_no_index(self):
        assert list_marketplace() == []


class TestInstallPlugin:
    def test_install(self, tmp_path):
        source = tmp_path / "source"
        target = tmp_path / "installed"
        source.mkdir()
        target.mkdir()
        _make_plugin_source(source, "my-plugin")
        plugin = install_plugin(source, target, "my-plugin")
        assert plugin.name == "my-plugin"
        assert (target / "my-plugin" / "plugin.json").exists()

    def test_install_nonexistent(self, tmp_path):
        with pytest.raises(PluginError, match="not found"):
            install_plugin(tmp_path / "nope", tmp_path, "missing")


class TestUninstallPlugin:
    def test_uninstall(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin_source(plugin_dir, "removeme")
        assert uninstall_plugin(plugin_dir, "removeme") is True
        assert not (plugin_dir / "removeme").exists()

    def test_uninstall_nonexistent(self, tmp_path):
        assert uninstall_plugin(tmp_path, "nope") is False
