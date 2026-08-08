"""JSON-RPC gateway socket for the ClawCodex Desktop app.

One WebSocket (``/api/ws``) carries the whole chat surface. Wire contract
(client: ``ui-desktop/packages/shared/src/json-rpc-gateway.ts``):

- client → server: ``{"jsonrpc":"2.0","id":"r<N>","method":M,"params":P}``
  (ids are opaque strings/numbers; echo them back untouched),
- server → client (reply): ``{"id":<same>,"result":R}`` or
  ``{"id":<same>,"error":{"message":str}}``,
- server → client (push): ``{"method":"event","params":{"type":T,
  "session_id":S?,"payload":P?}}`` — no ``id`` on pushes, ever (a pushed
  ``id`` would be swallowed by the client's pending-call map).

Sessions are the same in-process agents the TUI backend runs
(``make_spawn_agent``); this module only adapts wire shapes: JSON-RPC methods
→ agent inbound frames, agent outbound frames → gateway events. The
translation tables live in :mod:`src.server.desktop_gateway_translate`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from src.server.desktop_serve import DesktopServeState

logger = logging.getLogger(__name__)


async def _send_json(websocket: WebSocket, obj: dict[str, Any]) -> None:
    try:
        await websocket.send_json(obj)
    except Exception:  # noqa: BLE001 — a dying socket must not kill the pump
        pass


async def send_event(
    websocket: WebSocket,
    type_: str,
    payload: Any = None,
    session_id: str | None = None,
) -> None:
    params: dict[str, Any] = {"type": type_}
    if session_id is not None:
        params["session_id"] = session_id
    if payload is not None:
        params["payload"] = payload
    await _send_json(websocket, {"method": "event", "params": params})


async def handle_gateway_socket(websocket: WebSocket, state: DesktopServeState) -> None:
    """Accept one gateway socket and pump it until disconnect."""
    await websocket.accept()

    from src.server.desktop_gateway_methods import GatewayConnection

    conn = GatewayConnection(websocket=websocket, state=state)
    await conn.on_open()
    try:
        while True:
            try:
                frame = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001 — non-JSON frames are ignored
                continue
            if not isinstance(frame, dict):
                continue
            await _dispatch(conn, frame)
    finally:
        await conn.on_close()


async def _dispatch(conn: Any, frame: dict[str, Any]) -> None:
    method = frame.get("method")
    request_id = frame.get("id")
    params = frame.get("params") or {}
    if not isinstance(method, str):
        return

    handler = conn.method_handlers.get(method)
    if handler is None:
        if request_id is not None:
            await _send_json(
                conn.websocket,
                {"id": request_id, "error": {"message": f"method not found: {method}"}},
            )
        return

    try:
        result = await handler(params)
    except Exception as exc:  # noqa: BLE001 — one bad call must not drop the socket
        logger.warning("gateway: %s failed", method, exc_info=True)
        if request_id is not None:
            await _send_json(
                conn.websocket,
                {"id": request_id, "error": {"message": str(exc) or method}},
            )
        return

    if request_id is not None:
        await _send_json(conn.websocket, {"id": request_id, "result": result})


__all__ = ["handle_gateway_socket", "send_event"]
