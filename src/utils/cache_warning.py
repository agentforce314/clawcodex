"""Facade — utils/cache_warning.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing from
src.utils.cache_warning import … call sites continue to work
during the migration.  New code should import from
clawcodex_ext.utils.cache_warning directly.
"""

from clawcodex_ext.utils.cache_warning import (  # noqa: F401
    MAX_SOURCE_ENTRIES,
    CacheWarningState,
    CacheWarning,
)

__all__ = [
    "MAX_SOURCE_ENTRIES",
    "CacheWarningState",
    "CacheWarning",
]
