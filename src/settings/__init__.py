"""Settings system -- multi-source loading, validation, caching."""

from __future__ import annotations

# F-47: ``PermissionRule`` and ``validate_permission_rules`` were the
# settings-layer fake-typed list[PermissionRule] (Sub-H deletion). The
# runtime ``PermissionRule`` lives in ``src.permissions.types`` and is
# unrelated. Permission rule validation is now integrated into
# ``validate_settings`` (see ``src/settings/validation.py``).
from .types import SettingsSchema, ToolSettings
from .constants import DEFAULT_SETTINGS
from .settings import load_settings, get_settings, invalidate_settings_cache
from .validation import validate_settings, ValidationError
from .change_detector import SettingsChangeDetector, SettingsDiff
from .managed_path import resolve_managed_settings_path

__all__ = [
    "DEFAULT_SETTINGS",
    "SettingsChangeDetector",
    "SettingsDiff",
    "SettingsSchema",
    "ToolSettings",
    "ValidationError",
    "get_settings",
    "invalidate_settings_cache",
    "load_settings",
    "resolve_managed_settings_path",
    "validate_settings",
]
