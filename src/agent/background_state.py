"""Facade — background_state has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.background_state import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.background_state`` directly.
"""

from clawcodex_ext.agent.background_state import (  # noqa: F401
    background_signal,
    is_backgrounded,
    set_backgrounded,
    signal_background,
    reset_background,
)

__all__ = [
    "background_signal",
    "is_backgrounded",
    "set_backgrounded",
    "signal_background",
    "reset_background",
]
