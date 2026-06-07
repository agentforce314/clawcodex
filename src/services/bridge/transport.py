"""Facade — services/bridge/transport.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing from
src.services.bridge.transport import … call sites continue to work
during the migration.  New code should import from
clawcodex_ext.services.bridge.transport directly.
"""

from clawcodex_ext.services.bridge.transport import (  # noqa: F401
    BridgeMessage,
    BridgeTransport,
    WebSocketTransport,
)

__all__ = [
    "BridgeMessage",
    "BridgeTransport",
    "WebSocketTransport",
]
