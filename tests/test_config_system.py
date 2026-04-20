"""Tests for R2-WS-6: Config system — three-level hierarchy, atomic write, history."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import (
    ConfigManager,
    _atomic_write_json,
    _deep_merge,
    _read_json,
    append_history_entry,
    read_history_entries,
)


class TestDeepMerge:
    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        assert _deep_merge(base, override) == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_override_replaces_non_dict(self):
        base = {"a": [1, 2]}
        override = {"a": [3]}
        assert _deep_merge(base, override) == {"a": [3]}

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        override = {"a": 1}
        assert _deep_merge({}, override) == {"a": 1}

    def test_does_not_mutate_original(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        result = _deep_merge(base, override)
        assert "y" not in base["a"]
        assert result["a"]["y"] == 2


class TestAtomicWriteJson:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, {"key": "value"})
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {"key": "value"}

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "test.json"
        _atomic_write_json(path, {"nested": True})
        assert path.exists()

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, {"v": 1})
        _atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text()) == {"v": 2}


class TestReadJson:
    def test_reads_valid_json(self, tmp_path):
        path = tmp_path / "test.json"
        path.write_text('{"hello": "world"}')
        assert _read_json(path) == {"hello": "world"}

    def test_returns_empty_on_missing(self, tmp_path):
        path = tmp_path / "missing.json"
        assert _read_json(path) == {}

    def test_returns_empty_on_invalid(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json!")
        assert _read_json(path) == {}


class TestConfigManager:
    def test_load_global_default(self, tmp_path):
        """When global config doesn't exist, returns default config."""
        with patch("src.config.get_global_config_path", return_value=tmp_path / "missing.json"):
            mgr = ConfigManager()
            cfg = mgr.load_global()
            assert "default_provider" in cfg

    def test_save_and_load_global(self, tmp_path):
        path = tmp_path / "config.json"
        with patch("src.config.get_global_config_path", return_value=path):
            mgr = ConfigManager()
            mgr.save_global({"test_key": "test_value"})
            mgr.invalidate()
            cfg = mgr.load_global()
            assert cfg["test_key"] == "test_value"

    def test_merged_config_inheritance(self, tmp_path):
        global_path = tmp_path / "global.json"
        project_path = tmp_path / "project.json"
        local_path = tmp_path / "local.json"

        global_path.write_text('{"a": 1, "b": 2, "c": {"x": 10}}')
        project_path.write_text('{"b": 3, "c": {"y": 20}}')
        local_path.write_text('{"b": 4, "d": 5}')

        with patch("src.config.get_global_config_path", return_value=global_path), \
             patch("src.config.get_project_config_path", return_value=project_path), \
             patch("src.config.get_local_config_path", return_value=local_path):
            mgr = ConfigManager()
            merged = mgr.get_merged()
            assert merged["a"] == 1      # from global
            assert merged["b"] == 4      # local overrides project overrides global
            assert merged["c"]["x"] == 10  # deep merge: global
            assert merged["c"]["y"] == 20  # deep merge: project
            assert merged["d"] == 5      # from local

    def test_convenience_get(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('{"hello": "world"}')
        with patch("src.config.get_global_config_path", return_value=path), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            mgr = ConfigManager()
            assert mgr.get("hello") == "world"
            assert mgr.get("missing", "default") == "default"

    def test_cache_invalidation(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('{"v": 1}')
        with patch("src.config.get_global_config_path", return_value=path), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            mgr = ConfigManager()
            assert mgr.get("v") == 1

            path.write_text('{"v": 2}')
            # Still cached
            assert mgr.get("v") == 1

            mgr.invalidate()
            assert mgr.get("v") == 2


class TestHistory:
    def test_append_and_read(self, tmp_path):
        history_path = tmp_path / "history.jsonl"
        with patch("src.config.HISTORY_FILE", history_path):
            append_history_entry("first entry", source="test")
            append_history_entry("second entry", source="test")

            entries = read_history_entries()
            assert len(entries) == 2
            assert entries[0]["content"] == "first entry"
            assert entries[1]["content"] == "second entry"
            assert entries[0]["source"] == "test"

    def test_read_empty_returns_empty(self, tmp_path):
        history_path = tmp_path / "nonexistent.jsonl"
        with patch("src.config.HISTORY_FILE", history_path):
            assert read_history_entries() == []

    def test_read_limit(self, tmp_path):
        history_path = tmp_path / "history.jsonl"
        with patch("src.config.HISTORY_FILE", history_path):
            for i in range(10):
                append_history_entry(f"entry {i}")
            entries = read_history_entries(limit=3)
            assert len(entries) == 3
            assert entries[0]["content"] == "entry 7"

    def test_malformed_lines_skipped(self, tmp_path):
        history_path = tmp_path / "history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w") as f:
            f.write('{"content": "good"}\n')
            f.write('bad json line\n')
            f.write('{"content": "also good"}\n')

        with patch("src.config.HISTORY_FILE", history_path):
            entries = read_history_entries()
            assert len(entries) == 2
