"""Facade — settings/pydantic_adapter.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.settings.pydantic_adapter import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.settings.pydantic_adapter`` directly.
"""

from clawcodex_ext.settings.pydantic_adapter import (  # noqa: F401
    ProviderConfig,
    SessionConfig,
    ClawCodexSettings,
    load_settings_from_config_manager,
    settings_to_dict,
    dict_to_settings,
    get_cached_settings,
    invalidate_settings_cache,
    is_pydantic_settings_available,
    get_pydantic_settings_class,
)

__all__ = [
    "ProviderConfig",
    "SessionConfig",
    "ClawCodexSettings",
    "load_settings_from_config_manager",
    "settings_to_dict",
    "dict_to_settings",
    "get_cached_settings",
    "invalidate_settings_cache",
    "is_pydantic_settings_available",
    "get_pydantic_settings_class",
]
