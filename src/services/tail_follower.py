"""Facade — services/tail_follower.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.services.tail_follower import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.services.tail_follower`` directly.
"""

from clawcodex_ext.services.tail_follower import (  # noqa: F401
    TailFollower,
)

__all__ = [
    "TailFollower",
]
