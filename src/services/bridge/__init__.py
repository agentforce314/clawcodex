"""Bridge/Remote subsystem.

Provides remote session support, transport abstraction, and auth for
headless/remote Claude Code instances.
Mirrors TypeScript bridge/ directory.
"""
from __future__ import annotations

from .session import BridgeSession, BridgeSessionConfig, BridgeSessionState
from .transport import BridgeTransport, WebSocketTransport
from .auth import BridgeAuth, BridgeToken

__all__ = [
    "BridgeAuth",
    "BridgeSession",
    "BridgeSessionConfig",
    "BridgeSessionState",
    "BridgeToken",
    "BridgeTransport",
    "WebSocketTransport",
]
