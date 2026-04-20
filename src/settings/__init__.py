"""Settings system — multi-source loading, validation, caching."""

from __future__ import annotations

from .types import SettingsSchema, PermissionRule, ToolSettings
from .constants import DEFAULT_SETTINGS
from .settings import load_settings, get_settings, invalidate_settings_cache
from .validation import validate_settings, ValidationError
from .change_detector import SettingsChangeDetector, SettingsDiff
from .managed_path import resolve_managed_settings_path
from .permission_validation import validate_permission_rules

__all__ = [
    "DEFAULT_SETTINGS",
    "PermissionRule",
    "SettingsChangeDetector",
    "SettingsDiff",
    "SettingsSchema",
    "ToolSettings",
    "ValidationError",
    "get_settings",
    "invalidate_settings_cache",
    "load_settings",
    "resolve_managed_settings_path",
    "validate_permission_rules",
    "validate_settings",
]
