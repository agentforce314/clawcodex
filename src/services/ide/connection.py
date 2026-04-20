"""IDE connection management via JSON-RPC.

Mirrors TypeScript ide/connection.ts — manages the lifecycle of IDE connections
including connect, disconnect, and request/notification handling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from .types import IDEConnection, IDEType

logger = logging.getLogger(__name__)


@dataclass
class PendingRequest:
    """A pending JSON-RPC request awaiting a response."""
    id: int
    method: str
    future: asyncio.Future[Any]


class IDEConnectionManager:
    """Manages JSON-RPC connection to IDE extension.

    Handles connect/disconnect lifecycle, request routing, and capability
    negotiation. Only one IDE connection is active at a time.
    """

    def __init__(self) -> None:
        self._connection: IDEConnection = IDEConnection()
        self._request_id: int = 0
        self._pending: dict[int, PendingRequest] = {}
        self._handlers: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}
        self._notification_handlers: dict[str, list[Callable[..., Any]]] = {}
        self._on_connect: list[Callable[[IDEConnection], None]] = []
        self._on_disconnect: list[Callable[[], None]] = []

    @property
    def connection(self) -> IDEConnection:
        return self._connection

    @property
    def is_connected(self) -> bool:
        return self._connection.connected

    def connect(
        self,
        ide_type: IDEType,
        version: str = "",
        workspace_root: str = "",
        capabilities: dict[str, bool] | None = None,
    ) -> IDEConnection:
        """Establish a new IDE connection."""
        self._connection = IDEConnection(
            ide_type=ide_type,
            version=version,
            workspace_root=workspace_root,
            connected=True,
            capabilities=capabilities or {},
        )
        for cb in self._on_connect:
            try:
                cb(self._connection)
            except Exception:
                logger.exception("Error in on_connect callback")
        return self._connection

    def disconnect(self) -> None:
        """Close the IDE connection."""
        self._connection.connected = False
        # Cancel pending requests
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
        for cb in self._on_disconnect:
            try:
                cb()
            except Exception:
                logger.exception("Error in on_disconnect callback")

    def register_handler(
        self,
        method: str,
        handler: Callable[..., Coroutine[Any, Any, Any]],
    ) -> None:
        """Register a handler for incoming JSON-RPC requests."""
        self._handlers[method] = handler

    def on_notification(
        self,
        method: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        """Register a notification handler. Returns unsubscribe function."""
        if method not in self._notification_handlers:
            self._notification_handlers[method] = []
        self._notification_handlers[method].append(handler)

        def unsubscribe() -> None:
            handlers = self._notification_handlers.get(method, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> Any:
        """Send a JSON-RPC request to the IDE and await response."""
        if not self.is_connected:
            raise ConnectionError("No IDE connection")

        self._request_id += 1
        request_id = self._request_id

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = PendingRequest(
            id=request_id,
            method=method,
            future=future,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"IDE request {method} timed out after {timeout}s")

    def handle_response(self, request_id: int, result: Any = None, error: Any = None) -> None:
        """Handle an incoming JSON-RPC response."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            logger.warning("Received response for unknown request %d", request_id)
            return
        if error is not None:
            pending.future.set_exception(RuntimeError(str(error)))
        else:
            pending.future.set_result(result)

    async def handle_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Handle an incoming JSON-RPC request from the IDE."""
        handler = self._handlers.get(method)
        if handler is None:
            raise ValueError(f"No handler for method: {method}")
        return await handler(params or {})

    def handle_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Handle an incoming JSON-RPC notification."""
        handlers = self._notification_handlers.get(method, [])
        for handler in handlers:
            try:
                handler(params or {})
            except Exception:
                logger.exception("Error in notification handler for %s", method)

    def on_connect_callback(self, cb: Callable[[IDEConnection], None]) -> None:
        self._on_connect.append(cb)

    def on_disconnect_callback(self, cb: Callable[[], None]) -> None:
        self._on_disconnect.append(cb)
