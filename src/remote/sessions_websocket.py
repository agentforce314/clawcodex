"""WebSocket client for ``/v1/sessions/ws/{id}/subscribe`` (claude.ai → CCR).

Ports ``typescript/src/remote/SessionsWebSocket.ts:82-404``.

Reconnection strategy is **discriminated by close code** (per chapter
§"Remote Session Management"):

  - **4003 (unauthorized)**: stop immediately. Permanent rejection.
  - **4001 (session not found)**: max 3 retries with linear backoff
    (transient during compaction).
  - **other transient**: max 5 attempts with constant-delay retry
    (``RECONNECT_DELAY_SECONDS = 2.0``). [chapter, unverified: chapter
    says "exponential backoff", but the actual TS code uses a constant
    2 s delay; we match the code.]

Per Risk #22 in the refactoring plan: the ``websockets`` library does
NOT auto-reconnect (unlike JS WebSocket event handlers). We implement
an explicit reconnect loop with the discriminated retry strategy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import connect as ws_connect

from src.bridge.close_codes import (
    WS_CLOSE_PERMANENT_UNAUTHORIZED,
    WS_CLOSE_SESSION_NOT_FOUND,
)

logger = logging.getLogger(__name__)

#: Constant retry delay matching ``SessionsWebSocket.ts:17``.
RECONNECT_DELAY_SECONDS = 2.0

#: Max retries for the generic-transient path
#: (``SessionsWebSocket.ts:18`` ``MAX_RECONNECT_ATTEMPTS``).
MAX_RECONNECT_ATTEMPTS = 5

#: Max retries for the 4001 session-not-found path
#: (``SessionsWebSocket.ts:26`` ``MAX_SESSION_NOT_FOUND_RETRIES``).
MAX_SESSION_NOT_FOUND_RETRIES = 3

#: Application-level ping interval (websockets library handles
#: protocol-level pings; this constant matches TS for parity).
PING_INTERVAL_SECONDS = 30.0


GetAccessToken = Callable[[], str]


@dataclass
class SessionsWebSocketCallbacks:
    """Per-session event callbacks. Mirrors TS at lines 57-65.

    All callbacks may be sync or async; sync is just called, async is
    awaited via ``asyncio.iscoroutine`` check.
    """

    on_message: Callable[[dict], None | Awaitable[None]]
    on_close: Callable[[], None | Awaitable[None]] | None = None
    on_error: Callable[[Exception], None | Awaitable[None]] | None = None
    on_connected: Callable[[], None | Awaitable[None]] | None = None
    on_reconnecting: Callable[[], None | Awaitable[None]] | None = None


def _is_sessions_message(value: object) -> bool:
    """Permissive type guard: any dict with a string ``type`` field.

    Mirrors ``SessionsWebSocket.ts:46-55``. Deliberately permissive so
    new server-side message types don't get silently dropped before the
    Python client is updated.
    """
    return isinstance(value, dict) and isinstance(value.get('type'), str)


class SessionsWebSocket:
    """WS client for the claude.ai → CCR session-subscribe stream.

    Lifecycle:
        ws = SessionsWebSocket(session_id, org_uuid, get_token, callbacks)
        await ws.connect()           — opens the WS, spawns reader task
        await ws.send_control_response(resp)
        await ws.send_control_request(inner)
        ws.is_connected()
        await ws.disconnect()        — stop reconnecting, close the WS
        await ws.reconnect()         — force-close + immediately retry
    """

    def __init__(
        self,
        session_id: str,
        org_uuid: str,
        get_access_token: GetAccessToken,
        callbacks: SessionsWebSocketCallbacks,
        *,
        base_url: str = 'wss://api.anthropic.com',
        anthropic_version: str = '2023-06-01',
    ) -> None:
        self._session_id = session_id
        self._org_uuid = org_uuid
        self._get_access_token = get_access_token
        self._callbacks = callbacks
        self._base_url = base_url.rstrip('/')
        self._anthropic_version = anthropic_version

        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._closed = False
        self._reconnect_attempts = 0
        self._not_found_retries = 0

    # ─── State queries ────────────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WS and start the reader. Returns once open OR raises."""
        if self._closed:
            return
        url = (
            f'{self._base_url}/v1/sessions/ws/{self._session_id}/subscribe'
            f'?organization_uuid={self._org_uuid}'
        )
        headers = {
            'Authorization': f'Bearer {self._get_access_token()}',
            'anthropic-version': self._anthropic_version,
        }
        try:
            ws = await ws_connect(
                url,
                additional_headers=headers,
                ping_interval=PING_INTERVAL_SECONDS,
            )
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            await self._invoke(self._callbacks.on_error, exc)
            self._schedule_reconnect_or_close()
            return

        self._ws = ws
        self._reconnect_attempts = 0
        self._not_found_retries = 0
        await self._invoke(self._callbacks.on_connected)
        self._reader_task = asyncio.get_running_loop().create_task(
            self._read_loop(), name='sessions-ws-reader',
        )

    async def disconnect(self) -> None:
        """Permanent close — no reconnects, fire on_close."""
        self._closed = True
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        ws, self._ws = self._ws, None
        if self._reader_task is not None and not self._reader_task.done():
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

    async def reconnect(self) -> None:
        """Force close + immediate reconnect (resets retry counters).

        Used when the subscription is known stale (e.g., after the
        worker container shutdown the server detected on its side).
        """
        self._reconnect_attempts = 0
        self._not_found_retries = 0
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except (websockets.exceptions.ConnectionClosed, OSError):
                pass
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
                pass
        # 500 ms grace before reconnecting (matches TS).
        await asyncio.sleep(0.5)
        await self.connect()

    # ─── Send API ─────────────────────────────────────────────────────

    async def send_control_response(self, response: dict) -> None:
        """Send a ``control_response`` (e.g., permission decision)."""
        if self._ws is None or self._closed:
            logger.warning('[SessionsWebSocket] cannot send: not connected')
            return
        try:
            await self._ws.send(json.dumps(response))
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            logger.warning('[SessionsWebSocket] send failed: %s', exc)

    async def send_control_request(self, inner: dict) -> None:
        """Wrap ``inner`` in a ``control_request`` envelope and send."""
        if self._ws is None or self._closed:
            logger.warning('[SessionsWebSocket] cannot send: not connected')
            return
        envelope = {
            'type': 'control_request',
            'request_id': str(_uuid.uuid4()),
            'request': inner,
        }
        try:
            await self._ws.send(json.dumps(envelope))
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            logger.warning('[SessionsWebSocket] send failed: %s', exc)

    # ─── Read loop ────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for raw in ws:
                text = raw if isinstance(raw, str) else raw.decode('utf-8', errors='replace')
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    logger.debug('[SessionsWebSocket] parse error: %s', exc)
                    continue
                if not _is_sessions_message(parsed):
                    logger.debug('[SessionsWebSocket] ignoring non-message: %s', parsed)
                    continue
                await self._invoke(self._callbacks.on_message, parsed)
        except websockets.exceptions.ConnectionClosed as exc:
            close_code = exc.rcvd.code if exc.rcvd else None
            self._on_ws_close(close_code)
        except Exception as exc:  # noqa: BLE001
            await self._invoke(self._callbacks.on_error, exc)
            self._on_ws_close(None)

    def _on_ws_close(self, close_code: int | None) -> None:
        """Discriminated reconnect strategy.

        4003 → permanent stop, fire on_close.
        4001 → max 3 retries with linear backoff.
        other → max 5 attempts with constant delay.
        """
        if self._closed:
            return
        self._ws = None

        if close_code == WS_CLOSE_PERMANENT_UNAUTHORIZED:
            logger.debug(
                '[SessionsWebSocket] permanent close 4003; not reconnecting'
            )
            self._closed = True
            asyncio.get_running_loop().create_task(
                self._invoke(self._callbacks.on_close)
            )
            return

        if close_code == WS_CLOSE_SESSION_NOT_FOUND:
            self._not_found_retries += 1
            if self._not_found_retries > MAX_SESSION_NOT_FOUND_RETRIES:
                logger.debug(
                    '[SessionsWebSocket] 4001 retry budget exhausted; not reconnecting'
                )
                self._closed = True
                asyncio.get_running_loop().create_task(
                    self._invoke(self._callbacks.on_close)
                )
                return
            delay = RECONNECT_DELAY_SECONDS * self._not_found_retries
            self._schedule_reconnect_with_delay(delay)
            return

        # Generic transient.
        self._reconnect_attempts += 1
        if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            logger.debug('[SessionsWebSocket] not reconnecting (budget exhausted)')
            self._closed = True
            asyncio.get_running_loop().create_task(
                self._invoke(self._callbacks.on_close)
            )
            return
        self._schedule_reconnect_with_delay(RECONNECT_DELAY_SECONDS)

    def _schedule_reconnect_or_close(self) -> None:
        """Initial-connect failure path: same retry strategy as transient close."""
        if self._closed:
            return
        self._reconnect_attempts += 1
        if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            self._closed = True
            asyncio.get_running_loop().create_task(
                self._invoke(self._callbacks.on_close)
            )
            return
        self._schedule_reconnect_with_delay(RECONNECT_DELAY_SECONDS)

    def _schedule_reconnect_with_delay(self, delay_seconds: float) -> None:
        async def _do_reconnect() -> None:
            await self._invoke(self._callbacks.on_reconnecting)
            try:
                await asyncio.sleep(delay_seconds)
            except asyncio.CancelledError:
                return
            if self._closed:
                return
            await self.connect()

        loop = asyncio.get_running_loop()
        self._reconnect_task = loop.create_task(_do_reconnect(), name='sessions-ws-reconnect')

    # ─── Internal: callback helper ───────────────────────────────────

    @staticmethod
    async def _invoke(callback: Callable[..., object] | None, /, *args: object) -> None:
        if callback is None:
            return
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await result


__all__ = [
    'GetAccessToken',
    'MAX_RECONNECT_ATTEMPTS',
    'MAX_SESSION_NOT_FOUND_RETRIES',
    'PING_INTERVAL_SECONDS',
    'RECONNECT_DELAY_SECONDS',
    'SessionsWebSocket',
    'SessionsWebSocketCallbacks',
]
