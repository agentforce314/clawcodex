"""Bridge session management.

Mirrors TypeScript bridge/session.ts — manages remote session lifecycle.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class BridgeSessionState(str, Enum):
    INITIALIZING = "initializing"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class BridgeSessionConfig:
    """Configuration for a bridge session."""
    server_url: str = ""
    session_id: str = field(default_factory=lambda: uuid4().hex)
    auth_token: str = ""
    reconnect_attempts: int = 3
    reconnect_delay_ms: int = 1000
    heartbeat_interval_ms: int = 30000


@dataclass
class BridgeSession:
    """A remote bridge session."""
    config: BridgeSessionConfig = field(default_factory=BridgeSessionConfig)
    state: BridgeSessionState = BridgeSessionState.INITIALIZING
    connected_at: float | None = None
    last_heartbeat: float | None = None
    error: str | None = None

    @property
    def session_id(self) -> str:
        return self.config.session_id

    @property
    def is_connected(self) -> bool:
        return self.state == BridgeSessionState.CONNECTED

    def mark_connected(self) -> None:
        self.state = BridgeSessionState.CONNECTED
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.error = None

    def mark_disconnected(self, error: str | None = None) -> None:
        self.state = BridgeSessionState.ERROR if error else BridgeSessionState.DISCONNECTED
        self.error = error

    def heartbeat(self) -> None:
        self.last_heartbeat = time.time()
