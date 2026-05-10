"""High-level CCR session manager (claude.ai → CCR viewer/control).

Ports ``typescript/src/remote/RemoteSessionManager.ts:95-324``.

Wraps ``SessionsWebSocket`` + adds:

  - Per-``request_id`` tracking of pending permission requests
    (so ``control_cancel_request`` can find the right tool_use_id).
  - Permission round-trip: ``respond_to_permission_request`` builds
    the ``control_response`` envelope (allow→updated_input,
    deny→message).
  - ``cancel_session`` sends a ``control_request`` with subtype
    ``interrupt`` (skipped in viewer-only mode).
  - ``viewer_only`` flag — accepts the flag; ``cancel_session`` is a
    no-op when set. Other viewer-only behaviors (no 60 s response-stuck
    timeout, no title updates) live in the front-end consumer per A16.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.bridge.messaging import RemotePermissionResponse
from src.bridge.sdk_types import SDKControlPermissionRequest

from .sessions_websocket import (
    SessionsWebSocket,
    SessionsWebSocketCallbacks,
)

logger = logging.getLogger(__name__)


@dataclass
class RemoteSessionConfig:
    """Construction-time config. Mirrors TS at lines 50-62."""

    session_id: str
    get_access_token: Callable[[], str]
    org_uuid: str
    has_initial_prompt: bool = False
    #: Viewer-only mode (``claude assistant``): cancel_session is a no-op.
    viewer_only: bool = False


@dataclass
class RemoteSessionCallbacks:
    """Public callbacks. Mirrors TS at lines 64-85."""

    on_message: Callable[[dict], None | Awaitable[None]]
    on_permission_request: Callable[
        [SDKControlPermissionRequest, str], None | Awaitable[None]
    ]
    on_permission_cancelled: (
        Callable[[str, str | None], None | Awaitable[None]] | None
    ) = None
    on_connected: Callable[[], None | Awaitable[None]] | None = None
    on_disconnected: Callable[[], None | Awaitable[None]] | None = None
    on_reconnecting: Callable[[], None | Awaitable[None]] | None = None
    on_error: Callable[[Exception], None | Awaitable[None]] | None = None


class RemoteSessionManager:
    """High-level façade over ``SessionsWebSocket`` for one CCR session."""

    def __init__(
        self,
        config: RemoteSessionConfig,
        callbacks: RemoteSessionCallbacks,
        *,
        base_url: str = 'wss://api.anthropic.com',
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        self._base_url = base_url
        self._websocket: SessionsWebSocket | None = None
        self._pending_permission_requests: dict[str, SDKControlPermissionRequest] = {}

    # ─── Public API ────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the WS to ``/v1/sessions/ws/{id}/subscribe``.

        Mirrors ``RemoteSessionManager.ts:108-141``. Construction +
        connect are split so callers can wire callbacks first.
        """
        ws_callbacks = SessionsWebSocketCallbacks(
            on_message=self._handle_message,
            on_connected=self._callbacks.on_connected,
            on_close=self._callbacks.on_disconnected,
            on_reconnecting=self._callbacks.on_reconnecting,
            on_error=self._callbacks.on_error,
        )
        self._websocket = SessionsWebSocket(
            self._config.session_id,
            self._config.org_uuid,
            self._config.get_access_token,
            ws_callbacks,
            base_url=self._base_url,
        )
        # Schedule the connect; SessionsWebSocket.connect is async.
        loop = asyncio.get_running_loop()
        loop.create_task(self._websocket.connect(), name='remote-session-connect')

    def is_connected(self) -> bool:
        return self._websocket is not None and self._websocket.is_connected()

    async def send_message(self, content: object, *, uuid: str | None = None) -> bool:
        """Send a user prompt to the remote session.

        TS (``RemoteSessionManager.ts:219-242``) routes via HTTP POST to
        ``sendEventToRemoteSession`` (a teleport API). For Phase 4 we
        send via the WS itself for protocol simplicity; the wire shape
        matches what the agent expects on its stdin (``stream-json``).
        Returns False if the WS is not connected.
        """
        if self._websocket is None or not self._websocket.is_connected():
            return False
        envelope = {
            'type': 'user',
            'message': {'role': 'user', 'content': content},
            'parent_tool_use_id': None,
            'session_id': self._config.session_id,
        }
        if uuid is not None:
            envelope['uuid'] = uuid
        # Re-use the WS' send-control-response method to push raw JSON;
        # the WS doesn't distinguish between control envelopes and SDK
        # messages on send.
        await self._websocket.send_control_response(envelope)
        return True

    async def respond_to_permission_request(
        self,
        request_id: str,
        result: RemotePermissionResponse,
    ) -> None:
        """Reply to a ``can_use_tool`` request.

        Mirrors ``RemoteSessionManager.ts:247-282``. Builds the
        ``control_response`` envelope with the allow/deny payload.
        """
        if request_id not in self._pending_permission_requests:
            logger.warning(
                '[RemoteSessionManager] no pending permission request: %s',
                request_id,
            )
            return
        del self._pending_permission_requests[request_id]

        if result.behavior == 'allow':
            response_payload: dict[str, object] = {
                'behavior': 'allow',
                'updatedInput': getattr(result, 'updated_input', {}),
            }
        else:
            response_payload = {
                'behavior': 'deny',
                'message': getattr(result, 'message', ''),
            }
        envelope = {
            'type': 'control_response',
            'response': {
                'subtype': 'success',
                'request_id': request_id,
                'response': response_payload,
            },
        }
        if self._websocket is not None:
            await self._websocket.send_control_response(envelope)

    async def cancel_session(self) -> None:
        """Send an interrupt to the remote agent. No-op in viewer-only mode.

        Mirrors ``RemoteSessionManager.ts:294-297``. Viewer-only
        gating per A16: viewer_only callers must NOT send interrupts.
        """
        if self._config.viewer_only:
            logger.debug(
                '[RemoteSessionManager] viewer_only: cancel_session is a no-op'
            )
            return
        if self._websocket is None:
            return
        await self._websocket.send_control_request({'subtype': 'interrupt'})

    async def disconnect(self) -> None:
        """Close the WS and clear pending permission requests."""
        if self._websocket is not None:
            await self._websocket.disconnect()
            self._websocket = None
        self._pending_permission_requests.clear()

    async def reconnect(self) -> None:
        """Force-reconnect the WS (e.g., after stale subscription)."""
        if self._websocket is not None:
            await self._websocket.reconnect()

    def get_session_id(self) -> str:
        return self._config.session_id

    # ─── Internal: WS message routing ────────────────────────────────

    async def _handle_message(self, message: dict) -> None:
        """Route messages from the WS to the right consumer callback."""
        msg_type = message.get('type')

        if msg_type == 'control_request':
            await self._handle_control_request(message)
            return
        if msg_type == 'control_cancel_request':
            await self._handle_control_cancel(message)
            return
        if msg_type == 'control_response':
            logger.debug('[RemoteSessionManager] received control_response')
            return
        # All other SDK message types → on_message.
        await self._invoke(self._callbacks.on_message, message)

    async def _handle_control_request(self, message: dict) -> None:
        request_id = message.get('request_id')
        inner = message.get('request')
        if not isinstance(request_id, str) or not isinstance(inner, dict):
            return
        subtype = inner.get('subtype')
        if subtype == 'can_use_tool':
            self._pending_permission_requests[request_id] = inner  # type: ignore[assignment]
            await self._invoke(
                self._callbacks.on_permission_request,
                inner,  # type: ignore[arg-type]
                request_id,
            )
            return
        # Unknown subtype — send error so the server doesn't hang.
        logger.debug(
            '[RemoteSessionManager] unsupported control request subtype: %s', subtype
        )
        if self._websocket is not None:
            await self._websocket.send_control_response({
                'type': 'control_response',
                'response': {
                    'subtype': 'error',
                    'request_id': request_id,
                    'error': f'Unsupported control request subtype: {subtype}',
                },
            })

    async def _handle_control_cancel(self, message: dict) -> None:
        request_id = message.get('request_id')
        if not isinstance(request_id, str):
            return
        pending = self._pending_permission_requests.pop(request_id, None)
        tool_use_id = pending.get('tool_use_id') if pending else None
        await self._invoke(
            self._callbacks.on_permission_cancelled, request_id, tool_use_id
        )

    @staticmethod
    async def _invoke(callback: Callable[..., object] | None, /, *args: object) -> None:
        if callback is None:
            return
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await result


def create_remote_session_config(
    session_id: str,
    get_access_token: Callable[[], str],
    org_uuid: str,
    *,
    has_initial_prompt: bool = False,
    viewer_only: bool = False,
) -> RemoteSessionConfig:
    """Convenience helper mirroring ``createRemoteSessionConfig`` in TS."""
    return RemoteSessionConfig(
        session_id=session_id,
        get_access_token=get_access_token,
        org_uuid=org_uuid,
        has_initial_prompt=has_initial_prompt,
        viewer_only=viewer_only,
    )


__all__ = [
    'RemoteSessionCallbacks',
    'RemoteSessionConfig',
    'RemoteSessionManager',
    'create_remote_session_config',
]
