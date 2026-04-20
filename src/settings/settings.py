"""Settings loading, merging, and caching matching TypeScript settings/settings.ts."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

from ..config import ConfigManager, _deep_merge
from .constants import DEFAULT_SETTINGS
from .types import SettingsSchema

logger = logging.getLogger(__name__)

_settings_cache: SettingsSchema | None = None


def invalidate_settings_cache() -> None:
    """Clear the cached settings."""
    global _settings_cache
    _settings_cache = None


def load_settings(
    *,
    config_manager: ConfigManager | None = None,
    cwd: str | Path | None = None,
    extra_overrides: dict[str, Any] | None = None,
) -> SettingsSchema:
    """Load settings from config hierarchy + defaults.

    Merge order: DEFAULT_SETTINGS < global config "settings" < project < local < extra_overrides.
    """
    if config_manager is None:
        config_manager = ConfigManager(cwd=cwd)

    base = dataclasses.asdict(DEFAULT_SETTINGS)

    # Pull "settings" sub-key from each config level
    global_settings = config_manager.load_global().get("settings", {})
    project_settings = config_manager.load_project().get("settings", {})
    local_settings = config_manager.load_local().get("settings", {})

    merged = base
    if global_settings:
        merged = _deep_merge(merged, global_settings)
    if project_settings:
        merged = _deep_merge(merged, project_settings)
    if local_settings:
        merged = _deep_merge(merged, local_settings)
    if extra_overrides:
        merged = _deep_merge(merged, extra_overrides)

    return SettingsSchema.from_dict(merged)


def get_settings(
    *,
    config_manager: ConfigManager | None = None,
    cwd: str | Path | None = None,
) -> SettingsSchema:
    """Get cached settings (load on first call)."""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = load_settings(config_manager=config_manager, cwd=cwd)
    return _settings_cache
