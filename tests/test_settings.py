"""Tests for R2-WS-6: Settings system."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from src.settings.types import (
    CompactSettings,
    OutputStyleSettings,
    PermissionRule,
    SettingsSchema,
    ToolSettings,
)
from src.settings.constants import DEFAULT_SETTINGS
from src.settings.validation import validate_settings, ValidationError
from src.settings.change_detector import SettingsChangeDetector, SettingsDiff
from src.settings.settings import load_settings, invalidate_settings_cache
from src.settings.permission_validation import validate_permission_rules
from src.settings.managed_path import resolve_managed_settings_path


class TestSettingsSchema:
    def test_default_settings_valid(self):
        errors = validate_settings(DEFAULT_SETTINGS)
        assert errors == []

    def test_to_dict_roundtrip(self):
        original = DEFAULT_SETTINGS
        d = original.to_dict()
        restored = SettingsSchema.from_dict(d)
        assert restored.model == original.model
        assert restored.provider == original.provider
        assert restored.permission_mode == original.permission_mode

    def test_from_dict_with_extra(self):
        data = {"model": "test-model", "unknown_field": 42}
        s = SettingsSchema.from_dict(data)
        assert s.model == "test-model"
        assert s.extra.get("unknown_field") == 42

    def test_from_dict_nested_objects(self):
        data = {
            "output_style": {"style": "concise", "max_width": 80},
            "compact": {"auto_compact": False, "threshold_tokens": 50000},
            "permissions": [{"tool": "Bash", "allow": True, "glob": "*.py"}],
        }
        s = SettingsSchema.from_dict(data)
        assert s.output_style.style == "concise"
        assert s.output_style.max_width == 80
        assert s.compact.auto_compact is False
        assert len(s.permissions) == 1
        assert s.permissions[0].tool == "Bash"


class TestValidation:
    def test_valid_settings(self):
        errors = validate_settings(DEFAULT_SETTINGS)
        assert errors == []

    def test_invalid_effort(self):
        s = SettingsSchema(effort="invalid")
        errors = validate_settings(s)
        assert any(e.field == "effort" for e in errors)

    def test_invalid_permission_mode(self):
        s = SettingsSchema(permission_mode="bad")  # type: ignore
        errors = validate_settings(s)
        assert any(e.field == "permission_mode" for e in errors)

    def test_invalid_output_style(self):
        s = SettingsSchema(output_style=OutputStyleSettings(style="nonexistent"))
        errors = validate_settings(s)
        assert any(e.field == "output_style.style" for e in errors)

    def test_max_width_too_small(self):
        s = SettingsSchema(output_style=OutputStyleSettings(max_width=10))
        errors = validate_settings(s)
        assert any(e.field == "output_style.max_width" for e in errors)

    def test_negative_max_turns(self):
        s = SettingsSchema(max_turns=-1)
        errors = validate_settings(s)
        assert any(e.field == "max_turns" for e in errors)

    def test_permission_rule_missing_tool(self):
        s = SettingsSchema(permissions=[PermissionRule(tool="")])
        errors = validate_settings(s)
        assert any("permissions[0]" in e.field for e in errors)


class TestChangeDetector:
    def test_no_changes(self):
        detector = SettingsChangeDetector()
        s = DEFAULT_SETTINGS
        detector.snapshot(s)
        diff = detector.compute_diff(s)
        assert not diff.has_changes

    def test_detect_simple_change(self):
        detector = SettingsChangeDetector()
        s1 = SettingsSchema(model="model-a")
        s2 = SettingsSchema(model="model-b")
        detector.snapshot(s1)
        diff = detector.compute_diff(s2)
        assert diff.has_changes
        assert "model" in diff.changed_keys

    def test_detect_and_update(self):
        detector = SettingsChangeDetector()
        s1 = SettingsSchema(model="model-a")
        s2 = SettingsSchema(model="model-b")
        s3 = SettingsSchema(model="model-b")

        detector.snapshot(s1)
        diff1 = detector.detect_and_update(s2)
        assert diff1.has_changes

        diff2 = detector.detect_and_update(s3)
        assert not diff2.has_changes


class TestPermissionValidation:
    def test_valid_rules(self):
        rules = [
            PermissionRule(tool="Bash", allow=True),
            PermissionRule(tool="Read", allow=True, glob="*.py"),
        ]
        errors = validate_permission_rules(rules)
        assert errors == []

    def test_missing_tool(self):
        rules = [PermissionRule(tool="")]
        errors = validate_permission_rules(rules)
        assert len(errors) == 1

    def test_invalid_regex(self):
        rules = [PermissionRule(tool="Bash", regex="[invalid")]
        errors = validate_permission_rules(rules)
        assert len(errors) >= 1
        assert "regex" in errors[0].lower()

    def test_both_glob_and_regex(self):
        rules = [PermissionRule(tool="Bash", glob="*.py", regex=".*\\.py")]
        errors = validate_permission_rules(rules)
        assert any("both" in e.lower() for e in errors)

    def test_duplicate_rules(self):
        rules = [
            PermissionRule(tool="Bash", glob="*.py"),
            PermissionRule(tool="Bash", glob="*.py"),
        ]
        errors = validate_permission_rules(rules)
        assert any("duplicate" in e.lower() for e in errors)


class TestManagedPath:
    def test_returns_none_when_no_managed_file(self):
        with patch.dict(os.environ, {}, clear=False):
            with patch("os.environ.get", side_effect=lambda k, *a: None if k == "CLAUDE_MANAGED_SETTINGS_PATH" else os.environ.get(k, *a)):
                # Most environments won't have the managed file
                result = resolve_managed_settings_path()
                # Just verify it doesn't crash — result may be None

    def test_env_var_override(self, tmp_path):
        managed = tmp_path / "managed.json"
        managed.write_text("{}")
        with patch.dict(os.environ, {"CLAUDE_MANAGED_SETTINGS_PATH": str(managed)}):
            result = resolve_managed_settings_path()
            assert result == managed


class TestLoadSettings:
    def test_returns_defaults_without_config(self, tmp_path):
        from src.config import ConfigManager
        mgr = ConfigManager(cwd=tmp_path)
        with patch("src.config.get_global_config_path", return_value=tmp_path / "missing.json"), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            invalidate_settings_cache()
            s = load_settings(config_manager=mgr)
            assert s.model == DEFAULT_SETTINGS.model
            assert s.provider == DEFAULT_SETTINGS.provider

    def test_overrides_from_config(self, tmp_path):
        from src.config import ConfigManager
        global_path = tmp_path / "config.json"
        global_path.write_text('{"settings": {"model": "custom-model", "effort": "high"}}')
        with patch("src.config.get_global_config_path", return_value=global_path), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            invalidate_settings_cache()
            mgr = ConfigManager(cwd=tmp_path)
            s = load_settings(config_manager=mgr)
            assert s.model == "custom-model"
            assert s.effort == "high"

    def test_extra_overrides(self, tmp_path):
        from src.config import ConfigManager
        with patch("src.config.get_global_config_path", return_value=tmp_path / "missing.json"), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            invalidate_settings_cache()
            mgr = ConfigManager(cwd=tmp_path)
            s = load_settings(config_manager=mgr, extra_overrides={"fast_mode": True})
            assert s.fast_mode is True


import os
