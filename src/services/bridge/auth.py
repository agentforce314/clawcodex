"""Facade — services/bridge/auth.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing from
src.services.bridge.auth import … call sites continue to work
during the migration.  New code should import from
clawcodex_ext.services.bridge.auth directly.
"""

from clawcodex_ext.services.bridge.auth import (  # noqa: F401
    BridgeToken,
    BridgeAuth,
)

__all__ = [
    "BridgeToken",
    "BridgeAuth",
]
