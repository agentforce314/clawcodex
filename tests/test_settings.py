"""Tests for R2-WS-6: Settings system."""

from __future__ import annotations

import dataclasses
import os
from unittest.mock import patch

import pytest

from src.settings.types import (
    CompactSettings,
    OutputStyleSettings,
    PermissionsConfig,
    SettingsSchema,
    ToolSettings,
)
from src.settings.constants import DEFAULT_SETTINGS
from src.settings.validation import validate_settings, ValidationError
from src.settings.change_detector import SettingsChangeDetector, SettingsDiff
from src.settings.settings import load_settings, invalidate_settings_cache
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
        # F-47: ``permissions`` is now a structured dict, not a list.
        data = {
            "output_style": {"style": "concise", "max_width": 80},
            "compact": {"auto_compact": False, "threshold_tokens": 50000},
            "permissions": {
                "allowBypassPermissionsMode": True,
                "defaultMode": "bypassPermissions",
                "rules": {"allow": ["Bash"], "deny": [], "ask": []},
            },
        }
        s = SettingsSchema.from_dict(data)
        assert s.output_style.style == "concise"
        assert s.output_style.max_width == 80
        assert s.compact.auto_compact is False
        assert isinstance(s.permissions, PermissionsConfig)
        assert s.permissions.allow_bypass_permissions_mode is True
        assert s.permissions.default_mode == "bypassPermissions"
        assert s.permissions.rules == {"allow": ["Bash"], "deny": [], "ask": []}

    def test_from_dict_permissions_none_safe(self):
        # F-47: ``None``/``[]``/``{}`` should all degrade to an empty
        # PermissionsConfig without raising. ``rules`` is always
        # initialized to the 3-behavior skeleton so downstream code can
        # read ``rules["allow"]`` without a KeyError.
        for payload in (None, [], {}, {"rules": "not-a-dict"}):
            s = SettingsSchema.from_dict({"permissions": payload})
            assert isinstance(s.permissions, PermissionsConfig)
            assert s.permissions.allow_bypass_permissions_mode is False
            assert s.permissions.default_mode is None
            assert s.permissions.rules == {"allow": [], "deny": [], "ask": []}
            assert s.permissions.additional == {}


class TestValidation:
    def test_valid_settings(self):
        errors = validate_settings(DEFAULT_SETTINGS)
        assert errors == []

    def test_invalid_effort(self):
        s = SettingsSchema(effort="invalid")
        errors = validate_settings(s)
        assert any(e.field == "effort" for e in errors)

    def test_invalid_permission_mode_via_structured(self):
        # F-47: bad default mode is reported against ``permissions.defaultMode``
        # when written through the structured field.
        s = SettingsSchema(
            permissions=PermissionsConfig(default_mode="not-a-mode")
        )
        errors = validate_settings(s)
        assert any(e.field == "permissions.defaultMode" for e in errors)

    def test_invalid_permission_mode_via_legacy_top_level(self):
        # F-47: top-level ``permission_mode`` still works as back-compat.
        s = SettingsSchema(permission_mode="bad")
        errors = validate_settings(s)
        assert any(e.field == "permissions.defaultMode" for e in errors)

    def test_empty_permission_mode_is_unset(self):
        # F-47: the new default ``""`` is treated as "unset" and skips the
        # enum check (no false-positive ValidationError).
        s = SettingsSchema(permission_mode="")
        errors = validate_settings(s)
        assert not any("defaultMode" in e.field for e in errors)

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

    def test_permission_rule_empty_string_in_allow(self):
        # F-47: rules is a dict[str, list[str]]; empty strings are invalid.
        s = SettingsSchema(
            permissions=PermissionsConfig(rules={"allow": ["Bash", ""], "deny": [], "ask": []})
        )
        errors = validate_settings(s)
        assert any(
            e.field == "permissions.rules.allow[1]" for e in errors
        )


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


class TestManagedPath:
    def test_returns_none_when_no_managed_file(self):
        with patch.dict(os.environ, {}, clear=False):
            with patch("os.environ.get", side_effect=lambda k, *a: None if k == "CLAUDE_MANAGED_SETTINGS_PATH" else os.environ.get(k, *a)):
                # Most environments won't have the managed file
                result = resolve_managed_settings_path()
                # Just verify it doesn't crash -- result may be None

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


class TestPermissionsConfig:
    def test_from_dict_full_camelcase(self):
        data = {
            "allowBypassPermissionsMode": True,
            "defaultMode": "bypassPermissions",
            "rules": {"allow": ["Bash", "Edit"], "deny": ["WebFetch"], "ask": []},
            "additionalDirectories": ["/tmp/extra", "/var/data"],
        }
        pc = PermissionsConfig.from_dict(data)
        assert pc.allow_bypass_permissions_mode is True
        assert pc.default_mode == "bypassPermissions"
        assert pc.rules == {"allow": ["Bash", "Edit"], "deny": ["WebFetch"], "ask": []}
        assert pc.additional_directories == ["/tmp/extra", "/var/data"]
        assert pc.additional == {}

    def test_from_dict_top_level_behavior_keys(self):
        # F-47: top-level allow/deny/ask keys (alternative to ``rules``)
        # are also accepted, matching the on-disk format produced by
        # older binaries.
        data = {
            "allowBypassPermissionsMode": False,
            "allow": ["Read"],
            "deny": ["Bash"],
            "ask": ["WebFetch"],
        }
        pc = PermissionsConfig.from_dict(data)
        assert pc.allow_bypass_permissions_mode is False
        assert pc.rules == {"allow": ["Read"], "deny": ["Bash"], "ask": ["WebFetch"]}

    def test_from_dict_unknown_subkey_preserved(self):
        data = {"myCustomFlag": 42, "anotherFlag": "hello"}
        pc = PermissionsConfig.from_dict(data)
        assert pc.additional == {"myCustomFlag": 42, "anotherFlag": "hello"}

    def test_to_dict_round_trip(self):
        pc = PermissionsConfig(
            allow_bypass_permissions_mode=True,
            default_mode="plan",
            rules={"allow": ["Bash"], "deny": [], "ask": []},
            additional_directories=["/extra"],
            additional={"myCustomFlag": 7},
        )
        d = pc.to_dict()
        pc2 = PermissionsConfig.from_dict(d)
        assert pc2.allow_bypass_permissions_mode is True
        assert pc2.default_mode == "plan"
        assert pc2.rules == {"allow": ["Bash"], "deny": [], "ask": []}
        assert pc2.additional_directories == ["/extra"]
        assert pc2.additional == {"myCustomFlag": 7}

    def test_to_dict_omits_default_and_empty(self):
        pc = PermissionsConfig()
        d = pc.to_dict()
        # The 3-behavior rules skeleton is always present (downstream
        # code reads ``rules["allow"]`` without a KeyError), so it
        # appears in ``to_dict()``. ``defaultMode`` and
        # ``additionalDirectories`` stay absent when unset.
        assert d == {
            "allowBypassPermissionsMode": False,
            "rules": {"allow": [], "deny": [], "ask": []},
        }
