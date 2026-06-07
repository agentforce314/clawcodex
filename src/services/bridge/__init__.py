"""Facade — services/bridge/__init__.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.services.bridge.__init__ import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.services.bridge.__init__`` directly.
"""

from clawcodex_ext.services.bridge.__init__ import (  # noqa: F401
    BridgeAuth,
    BridgeSession,
    BridgeSessionConfig,
    BridgeSessionState,
    BridgeToken,
    BridgeTransport,
    WebSocketTransport,
)

__all__ = [
    "BridgeAuth",
    "BridgeSession",
    "BridgeSessionConfig",
    "BridgeSessionState",
    "BridgeToken",
    "BridgeTransport",
    "WebSocketTransport",
]
