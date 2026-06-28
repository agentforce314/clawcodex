"""WebSocket client for Direct Connect sessions.

Ports ``typescript/src/server/directConnectManager.ts:40-213``.

Connects to the WS URL returned by ``create_direct_connect_session``,
reads NDJSON messages, branches by ``type``:

  - ``control_request`` with subtype ``can_use_tool`` → permission
    request callback (the only request the local server sends).
  - ``control_request`` with any other subtype → error response so the
    server doesn't hang.
  - Other SDK messages (assistant, result, system) → on_message callback.
  - ``control_response``, ``keep_alive``, ``control_cancel_request``,
    ``streamlined_*``, ``system{subtype:'post_turn_summary'}`` → drop.

Send side: ``send_message`` (user prompt), ``respond_to_permission_request``
(allow/deny + updated_input/message), ``send_interrupt`` (cancel).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import connect as ws_connect

from src.bridge.messaging import RemotePermissionResponse
from src.bridge.sdk_types import SDKControlPermissionRequest, SDKMessage

from .direct_connect_session import DirectConnectConfig

logger = logging.getLogger(__name__)


@dataclass
class DirectConnectCallbacks:
    """Per-session event callbacks.

    Mirrors TS ``DirectConnectCallbacks`` at
    ``directConnectManager.ts:20-29``.
    """

    on_message: Callable[[SDKMessage], None | Awaitable[None]]
    on_permission_request: Callable[[SDKControlPermissionRequest, str], None | Awaitable[None]]
    on_connected: Callable[[], None | Awaitable[None]] | None = None
    on_disconnected: Callable[[], None | Awaitable[None]] | None = None
    on_error: Callable[[Exception], None | Awaitable[None]] | None = None


def _is_stdout_message(value: object) -> bool:
    """True for ``{type: str}`` payloads — pre-narrowing guard."""
    return (
        isinstance(value, dict)
        and 'type' in value
        and isinstance(value.get('type'), str)
    )


# Message types that the manager filters out (server-internal noise that
# the local CLI does not need to surface). Mirrors the inverted check at
# ``directConnectManager.ts:104-110``.
_FILTERED_TYPES = frozenset({
    'control_response',
    'keep_alive',
    'control_cancel_request',
    'streamlined_text',
    'streamlined_tool_use_summary',
})


class DirectConnectSessionManager:
    """One Direct Connect WS session.

    Lifecycle:
        connect()      → opens WS, spawns reader task, fires on_connected
        send_message() → POST a user prompt over the WS
        respond_to_permission_request() → reply to a can_use_tool prompt
        send_interrupt() → cancel the in-flight tool call
        disconnect()   → close WS + cancel reader task
    """

    def __init__(
        self,
        config: DirectConnectConfig,
        callbacks: DirectConnectCallbacks,
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        # Set once on_disconnected has fired, to prevent double-fire when
        # both the reader's finally AND disconnect() try to invoke it.
        self._disconnected_fired = False

    # ─── Public API ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WS and start the reader. Resolves once the WS is open.

        Raises ``websockets.exceptions.WebSocketException`` (and OSError
        subclasses) on connect failure; does NOT swallow — caller can
        retry or surface the error.
        """
        headers: dict[str, str] = {}
        if self._config.auth_token:
            headers['authorization'] = f'Bearer {self._config.auth_token}'

        ws = await ws_connect(
            self._config.ws_url,
            additional_headers=headers,
        )
        self._ws = ws
        self._reader_task = asyncio.get_running_loop().create_task(
            self._read_loop(),
            name='direct-connect-reader',
        )
        await self._invoke(self._callbacks.on_connected)

    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed

    async def send_message(self, content: object, ephemeral: bool = False) -> bool:
        """Send a user prompt over the WS.

        Mirrors ``directConnectManager.ts:125-142``. The wire shape
        matches what the agent's stream-json input format expects.
        ``ephemeral=True`` marks a /btw side question (answered with context but
        not persisted to history). Returns False if the WS is not open.
        """
        if self._ws is None or self._closed:
            return False
        envelope: dict = {
            'type': 'user',
            'message': {'role': 'user', 'content': content},
            'parent_tool_use_id': None,
            'session_id': '',
        }
        if ephemeral:
            envelope['ephemeral'] = True
        try:
            await self._ws.send(json.dumps(envelope))
        except (websockets.exceptions.ConnectionClosed, OSError):
            return False
        return True

    async def respond_to_permission_request(
        self,
        request_id: str,
        result: RemotePermissionResponse,
    ) -> None:
        """Reply to a ``can_use_tool`` control_request.

        Mirrors ``directConnectManager.ts:144-167``. The wire shape is a
        ``control_response`` with ``subtype: 'success'`` carrying the
        allow/deny payload.
        """
        if self._ws is None or self._closed:
            return
        if result.behavior == 'allow':
            response: dict[str, object] = {
                'behavior': 'allow',
                'updatedInput': getattr(result, 'updated_input', {}),
            }
        else:
            response = {
                'behavior': 'deny',
                'message': getattr(result, 'message', ''),
            }
        envelope = {
            'type': 'control_response',
            'response': {
                'subtype': 'success',
                'request_id': request_id,
                'response': response,
            },
        }
        try:
            await self._ws.send(json.dumps(envelope))
        except (websockets.exceptions.ConnectionClosed, OSError):
            pass

    async def send_interrupt(self) -> None:
        """Send a control_request with subtype ``interrupt``.

        Mirrors ``directConnectManager.ts:172-186``. The local server
        cancels the in-flight tool call and returns the agent loop to
        an awaiting-user-input state.
        """
        if self._ws is None or self._closed:
            return
        envelope = {
            'type': 'control_request',
            'request_id': str(_uuid.uuid4()),
            'request': {'subtype': 'interrupt'},
        }
        try:
            await self._ws.send(json.dumps(envelope))
        except (websockets.exceptions.ConnectionClosed, OSError):
            pass

    async def disconnect(self) -> None:
        """Close the WS and cancel the reader task.

        Fires ``on_disconnected`` exactly once — before cancelling the
        reader, since the reader's ``finally``-block fire would itself
        get cancelled mid-await and never reach the callback.
        """
        self._closed = True
        ws, self._ws = self._ws, None
        # Fire on_disconnected first (sync-or-coroutine), so cancellation
        # of the reader can't pre-empt it. The flag prevents double-fire
        # when the reader's natural finally also tries to invoke it.
        await self._fire_disconnected_once()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
                pass
        if ws is not None:
            try:
                await ws.close()
            except (websockets.exceptions.ConnectionClosed, OSError):
                pass

    async def _fire_disconnected_once(self) -> None:
        if self._disconnected_fired:
            return
        self._disconnected_fired = True
        await self._invoke(self._callbacks.on_disconnected)

    # ─── Internal: message routing ─────────────────────────────────────

    async def _read_loop(self) -> None:
        """Pump WS messages until disconnect or error.

        Mirrors ``directConnectManager.ts:64-122``. Handles NDJSON-on-WS
        (each WS frame may contain multiple `\\n`-delimited messages, or
        a single message; both shapes work).
        """
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    text = raw.decode('utf-8', errors='replace')
                else:
                    text = raw
                # Split on newlines so a single WS frame containing
                # multiple NDJSON lines is handled correctly. Empty
                # lines are skipped.
                for line in text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not _is_stdout_message(parsed):
                        continue
                    await self._dispatch(parsed)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 -- callback decides how to surface
            await self._invoke(self._callbacks.on_error, exc)
        finally:
            await self._fire_disconnected_once()

    async def _dispatch(self, msg: dict[str, object]) -> None:
        """Branch one parsed message to the right callback."""
        msg_type = msg.get('type')

        if msg_type == 'control_request':
            await self._handle_control_request(msg)
            return

        if msg_type == 'system' and msg.get('subtype') == 'post_turn_summary':
            return  # filtered

        if msg_type in _FILTERED_TYPES:
            return  # filtered

        await self._invoke(self._callbacks.on_message, msg)  # type: ignore[arg-type]

    async def _handle_control_request(self, msg: dict[str, object]) -> None:
        """Route a server → client control_request.

        Only ``can_use_tool`` is recognized. Other subtypes get an error
        response so the server doesn't hang waiting for a reply that
        never comes (chapter explicit pattern).
        """
        request_id = msg.get('request_id')
        inner = msg.get('request')
        if not isinstance(request_id, str) or not isinstance(inner, dict):
            return
        subtype = inner.get('subtype')
        if subtype == 'can_use_tool':
            await self._invoke(
                self._callbacks.on_permission_request,
                inner,  # type: ignore[arg-type]
                request_id,
            )
            return
        # Unknown — send error response so the server doesn't hang.
        logger.debug(
            '[DirectConnect] Unsupported control request subtype: %s', subtype
        )
        await self._send_error_response(
            request_id, f'Unsupported control request subtype: {subtype}'
        )

    async def _send_error_response(self, request_id: str, error: str) -> None:
        """Send a ``control_response`` with ``subtype: 'error'``."""
        if self._ws is None or self._closed:
            return
        envelope = {
            'type': 'control_response',
            'response': {
                'subtype': 'error',
                'request_id': request_id,
                'error': error,
            },
        }
        try:
            await self._ws.send(json.dumps(envelope))
        except (websockets.exceptions.ConnectionClosed, OSError):
            pass

    # ─── Internal: callback invocation helper ─────────────────────────

    @staticmethod
    async def _invoke(callback: Callable[..., object] | None, /, *args: object) -> None:
        """Call a callback; await if it returns a coroutine."""
        if callback is None:
            return
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await result


__all__ = [
    'DirectConnectCallbacks',
    'DirectConnectSessionManager',
]
