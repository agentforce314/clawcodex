"""Facade — hooks/_pluggy_adapter.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.hooks._pluggy_adapter import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.hooks._pluggy_adapter`` directly.
"""

from clawcodex_ext.hooks._pluggy_adapter import (  # noqa: F401
    is_pluggy_available,
    ClawCodexHooks,
    PluggyHookManager,
    HookPluginAdapter,
)

__all__ = [
    "is_pluggy_available",
    "ClawCodexHooks",
    "PluggyHookManager",
    "HookPluginAdapter",
]
