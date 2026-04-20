"""Bridge transport layer.

Mirrors TypeScript bridge/transport.ts — abstract transport for bridge communication.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable


@dataclass
class BridgeMessage:
    """A message sent over the bridge transport."""
    type: str
    payload: dict[str, Any]
    id: str = ""


class BridgeTransport(ABC):
    """Abstract base for bridge transport implementations."""

    @abstractmethod
    async def connect(self, url: str, headers: dict[str, str] | None = None) -> None:
        """Connect to the bridge server."""

    @abstractmethod
    async def send(self, message: BridgeMessage) -> None:
        """Send a message to the bridge server."""

    @abstractmethod
    async def receive(self) -> AsyncGenerator[BridgeMessage, None]:
        """Receive messages from the bridge server."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""


class WebSocketTransport(BridgeTransport):
    """WebSocket-based bridge transport (stub implementation).

    Full implementation requires a WebSocket library (e.g., websockets).
    """

    def __init__(self) -> None:
        self._connected = False
        self._url: str = ""

    async def connect(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._connected = True

    async def send(self, message: BridgeMessage) -> None:
        if not self._connected:
            raise ConnectionError("Not connected")

    async def receive(self) -> AsyncGenerator[BridgeMessage, None]:
        if not self._connected:
            return
        # Stub: would yield incoming messages from WebSocket
        return
        yield  # type: ignore[misc]

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
