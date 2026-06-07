"""Facade — providers/runtime.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.providers.runtime import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.providers.runtime`` directly.
"""

from clawcodex_ext.providers.runtime import (  # noqa: F401
    OAUTH_PROVIDERS,
    build_provider_from_config,
)

__all__ = [
    "OAUTH_PROVIDERS",
    "build_provider_from_config",
]
