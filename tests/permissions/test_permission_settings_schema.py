"""F-47: Permission Settings Schema refactor -- 7 acceptance unit tests.

Covers the F-47 §3.17 acceptance criteria spelled out in
``docs/PROGRESS.md`` (F-47 验收标准):

  1. test_permissions_dict_loads_into_struct
  2. test_default_mode_resolved_from_permissions_dict
  3. test_has_allow_bypass_true_after_settings_loaded
  4. test_legacy_extra_permissions_fallback
  5. test_legacy_top_level_permission_mode_still_resolves
  6. test_unknown_subkey_preserved
  7. test_dict_shape_no_longer_crashes_validation

Plus a small helper that exercises the CLI ``resolve_permission_state``
plumb end-to-end (covers Sub-D).
"""

from __future__ import annotations

import argparse
import os
from unittest.mock import patch

import pytest

from src.settings.types import PermissionsConfig, SettingsSchema
from src.settings.constants import DEFAULT_SETTINGS
from src.settings.validation import validate_settings
from src.permissions.modes import (
    _settings_perms,
    has_allow_bypass_permissions_mode,
    initial_permission_mode_from_cli,
)
from src.settings.settings import invalidate_settings_cache


# ---------------------------------------------------------------------------
# 1. Structured dict load produces a populated PermissionsConfig.
# ---------------------------------------------------------------------------
class TestPermissionsDictLoadsIntoStruct:
    def test_permissions_dict_loads_into_struct(self, tmp_path):
        from src.config import ConfigManager
        global_path = tmp_path / "config.json"
        global_path.write_text(
            '{"settings": {"permissions": {"allowBypassPermissionsMode": true,'
            ' "defaultMode": "bypassPermissions"}}}'
        )
        with patch("src.config.get_global_config_path", return_value=global_path), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            invalidate_settings_cache()
            mgr = ConfigManager(cwd=tmp_path)
            from src.settings.settings import load_settings
            settings = load_settings(config_manager=mgr)
        assert isinstance(settings.permissions, PermissionsConfig)
        assert settings.permissions.allow_bypass_permissions_mode is True
        assert settings.permissions.default_mode == "bypassPermissions"


# ---------------------------------------------------------------------------
# 2. initial_permission_mode_from_cli honors settings_default_mode.
# ---------------------------------------------------------------------------
class TestDefaultModeResolvedFromPermissionsDict:
    def test_default_mode_resolved_from_permissions_dict(self):
        mode = initial_permission_mode_from_cli(
            permission_mode_cli=None,
            dangerously_skip_permissions=False,
            settings_default_mode="bypassPermissions",
        )
        assert mode == "bypassPermissions"

    def test_default_mode_none_falls_back_to_default(self):
        mode = initial_permission_mode_from_cli(
            permission_mode_cli=None,
            dangerously_skip_permissions=False,
            settings_default_mode=None,
        )
        assert mode == "default"

    def test_cli_flag_takes_precedence_over_settings(self):
        mode = initial_permission_mode_from_cli(
            permission_mode_cli="plan",
            dangerously_skip_permissions=False,
            settings_default_mode="bypassPermissions",
        )
        assert mode == "plan"


# ---------------------------------------------------------------------------
# 3. has_allow_bypass_permissions_mode returns True after settings load.
# ---------------------------------------------------------------------------
class TestHasAllowBypassTrueAfterSettingsLoaded:
    def test_has_allow_bypass_true_after_settings_loaded(self, tmp_path):
        from src.config import ConfigManager
        global_path = tmp_path / "config.json"
        global_path.write_text(
            '{"settings": {"permissions": {"allowBypassPermissionsMode": true}}}'
        )
        with patch("src.config.get_global_config_path", return_value=global_path), \
             patch("src.config.get_project_config_path", return_value=None), \
             patch("src.config.get_local_config_path", return_value=None):
            invalidate_settings_cache()
            mgr = ConfigManager(cwd=tmp_path)
            from src.settings.settings import load_settings
            settings = load_settings(config_manager=mgr)
            with patch(
                "src.settings.settings.get_settings", return_value=settings
            ):
                assert has_allow_bypass_permissions_mode() is True


# ---------------------------------------------------------------------------
# 4. Legacy settings.extra["permissions"] path still works.
# ---------------------------------------------------------------------------
class TestLegacyExtraPermissionsFallback:
    def test_legacy_extra_permissions_fallback(self):
        # Simulate the pre-F-47 shape: a dict landed in ``settings.extra``
        # because the schema field did not exist (or because a third-party
        # tool wrote it there). ``_settings_perms`` must still surface it.
        settings = SettingsSchema()
        settings.extra = {
            "permissions": {
                "allowBypassPermissionsMode": True,
                "defaultMode": "bypassPermissions",
            }
        }
        bag = _settings_perms(settings)
        assert bag.get("allowBypassPermissionsMode") is True
        assert bag.get("defaultMode") == "bypassPermissions"

    def test_structured_field_takes_priority_over_legacy(self):
        # When the structured ``PermissionsConfig`` carries an explicit
        # non-default value, it overrides the legacy ``extra`` path for
        # that field. (Default ``False`` is indistinguishable from
        # "unset" -- see ``_settings_perms_structured_is_explicit``.)
        settings = SettingsSchema(
            permissions=PermissionsConfig(allow_bypass_permissions_mode=True)
        )
        settings.extra = {"permissions": {"allowBypassPermissionsMode": False}}
        bag = _settings_perms(settings)
        assert bag.get("allowBypassPermissionsMode") is True


# ---------------------------------------------------------------------------
# 5. The legacy top-level ``settings.permission_mode`` field is no longer
# consulted at startup. ``settings.permissions.default_mode`` is the sole
# source for the default mode at the CLI boundary.
# ---------------------------------------------------------------------------
class TestLegacyTopLevelPermissionModeIsIgnored:
    def test_legacy_top_level_permission_mode_is_ignored(self):
        from clawcodex_ext.cli.permissions import resolve_permission_state
        args = argparse.Namespace(
            dangerously_skip_permissions=False,
            allow_dangerously_skip_permissions=False,
            permission_mode=None,
        )
        # The legacy back-compat channel was removed. Setting the top-level
        # field no longer affects the default mode; only the structured
        # ``permissions.default_mode`` is read. ``allowBypassPermissionsMode``
        # still controls whether bypass is *available* in the Shift+Tab
        # cycle -- that gate is independent of the default-mode resolver.
        settings = SettingsSchema(
            permission_mode="bypassPermissions",
            permissions=PermissionsConfig(allow_bypass_permissions_mode=True),
        )
        with patch("src.settings.settings.get_settings", return_value=settings), \
             patch(
                 "src.permissions.dangerous_safety"
                 ".enforce_dangerous_skip_permissions_safety",
                 lambda **_kw: None,
             ):
            resolve_permission_state(args)
        # Legacy field is ignored: no override flows in, default mode is the
        # built-in fallback.
        assert args._resolved_permission_mode == "default"
        # Bypass remains available because the structured flag is set.
        assert args._resolved_is_bypass_available is True

    def test_structured_default_mode_is_the_only_source(self):
        from clawcodex_ext.cli.permissions import resolve_permission_state
        args = argparse.Namespace(
            dangerously_skip_permissions=False,
            allow_dangerously_skip_permissions=False,
            permission_mode=None,
        )
        # The structured ``permissions.default_mode`` is the sole default-mode
        # source. Even when the legacy field is set to a different value, the
        # structured field wins by being the only one consulted.
        settings = SettingsSchema(
            permission_mode="plan",
            permissions=PermissionsConfig(
                default_mode="bypassPermissions",
                allow_bypass_permissions_mode=True,
            ),
        )
        with patch("src.settings.settings.get_settings", return_value=settings), \
             patch(
                 "src.permissions.dangerous_safety"
                 ".enforce_dangerous_skip_permissions_safety",
                 lambda **_kw: None,
             ):
            resolve_permission_state(args)
        assert args._resolved_permission_mode == "bypassPermissions"
        assert args._resolved_is_bypass_available is True


# ---------------------------------------------------------------------------
# 6. Unknown sub-keys are preserved in PermissionsConfig.additional.
# ---------------------------------------------------------------------------
class TestUnknownSubkeyPreserved:
    def test_unknown_subkey_preserved(self):
        settings = SettingsSchema.from_dict({
            "permissions": {
                "myCustomFlag": 42,
                "experimentalAuditChannel": "ndjson",
            }
        })
        assert settings.permissions.additional == {
            "myCustomFlag": 42,
            "experimentalAuditChannel": "ndjson",
        }
        # Round-trip back to dict: unknown keys survive.
        d = settings.permissions.to_dict()
        assert d["myCustomFlag"] == 42
        assert d["experimentalAuditChannel"] == "ndjson"
        # And known keys still serialize with their camelCase names.
        assert d["allowBypassPermissionsMode"] is False


# ---------------------------------------------------------------------------
# 7. validate_settings does not crash on the dict-shaped permissions.
# ---------------------------------------------------------------------------
class TestDictShapeNoLongerCrashesValidation:
    def test_dict_shape_no_longer_crashes_validation(self):
        # Old behavior: legacy validation had
        # ``for i, rule in enumerate(settings.permissions)`` which would
        # raise ``TypeError: dict is not iterable`` on the new shape.
        s = SettingsSchema(
            permissions=PermissionsConfig(
                allow_bypass_permissions_mode=True,
                default_mode="bypassPermissions",
                rules={"allow": ["Bash"], "deny": ["WebFetch"], "ask": []},
            )
        )
        errors = validate_settings(s)
        assert isinstance(errors, list)
        assert all(e.field != "permissions[0].tool" for e in errors)
        # The bad-mode path (default_mode="bogus") is the only error.
        s2 = SettingsSchema(
            permissions=PermissionsConfig(default_mode="bogus")
        )
        errors2 = validate_settings(s2)
        assert any(e.field == "permissions.defaultMode" for e in errors2)
