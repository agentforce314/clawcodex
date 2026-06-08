"""Facade — providers/runtime.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.providers.runtime import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.providers.runtime`` directly.
"""

from clawcodex_ext.providers.runtime import (  # noqa: F401
    OAUTH_PROVIDERS,
    build_provider_from_config,
    get_provider_config,
    resolve_codex_runtime_credentials,
)

# create_provider is now in clawcodex_ext.providers.factory; re-export
# for backward compat with old ``from src.providers.runtime import create_provider``.
from clawcodex_ext.providers.factory import create_provider  # noqa: F401

__all__ = [
    "OAUTH_PROVIDERS",
    "build_provider_from_config",
    "get_provider_config",
    "resolve_codex_runtime_credentials",
    "create_provider",
]
