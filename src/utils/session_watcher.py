"""Facade — utils/session_watcher.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.utils.session_watcher import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.utils.session_watcher`` directly.
"""

from clawcodex_ext.utils.session_watcher import (  # noqa: F401
    SessionWatcher,
)

__all__ = [
    "SessionWatcher",
]
