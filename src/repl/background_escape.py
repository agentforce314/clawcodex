"""Facade — repl/background_escape.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.repl.background_escape import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.repl.background_escape`` directly.
"""

from clawcodex_ext.repl.background_escape import (  # noqa: F401
    BackgroundEscape,
)

__all__ = [
    "BackgroundEscape",
]
