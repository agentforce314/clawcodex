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


def get_persisted_model(
    provider_name: str | None,
    *,
    cwd: str | Path | None = None,
) -> str | None:
    """The user's persisted /model choice for ``provider_name``, or None.

    Restore channel for #280: entrypoints resolve the startup model as
    ``cli option > persisted model > provider default_model``. The model
    is restored only when the persisted ``model_provider`` pairing
    matches the launch provider — model names are provider-scoped in a
    multi-provider config, and feeding provider B a model persisted on
    provider A would fail the first API call.

    Reads the config layers directly (most specific non-empty model
    wins: local > project > global; an empty ``model`` does NOT mask a
    less-specific layer) rather than the merged ``SettingsSchema`` — the
    schema bakes in a DEFAULT_SETTINGS.model, which must NOT override a
    provider's configured default for users who never ran /model.
    Fail-soft — a broken settings file must not block startup.
    """
    try:
        manager = ConfigManager(cwd=cwd)
        for cfg in (
            manager.load_local(),
            manager.load_project(),
            manager.load_global(),
        ):
            section = cfg.get("settings")
            if isinstance(section, dict) and section.get("model"):
                persisted_provider = section.get("model_provider") or ""
                if provider_name and persisted_provider == provider_name:
                    return str(section["model"])
                # Unpaired or mismatched: the persisted model belongs to
                # another provider (or predates the pairing) — fall back
                # to the provider's own default.
                return None
        return None
    except Exception:
        return None
