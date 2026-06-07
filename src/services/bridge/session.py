"""Facade — services/bridge/session.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing from
src.services.bridge.session import … call sites continue to work
during the migration.  New code should import from
clawcodex_ext.services.bridge.session directly.
"""

from clawcodex_ext.services.bridge.session import (  # noqa: F401
    BridgeSessionState,
    BridgeSessionConfig,
    BridgeSession,
)

__all__ = [
    "BridgeSessionState",
    "BridgeSessionConfig",
    "BridgeSession",
]
