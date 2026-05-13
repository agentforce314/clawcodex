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

Lifecycle state machine (round-2): the WS lives in one of three states
— ``closed``, ``connecting``, ``connected``. ``connecting`` exists
purely to make ``connect()`` single-flight; ``connected`` gates the
send paths. Mirrors TS ``WebSocketState`` at
``SessionsWebSocket.ts:38``. A separate ``_user_disconnected`` latch
distinguishes a *transient* closed state (waiting on a budgeted
reconnect timer) from a *permanent* closed state (user called
``disconnect()``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import websockets
from websockets.asyncio.client import connect as ws_connect

from src.bridge.close_codes import (
    WS_CLOSE_PERMANENT_UNAUTHORIZED,
    WS_CLOSE_SESSION_NOT_FOUND,
)

logger = logging.getLogger(__name__)

#: Tristate WS lifecycle. Mirrors TS ``WebSocketState`` at
#: ``SessionsWebSocket.ts:38``. ``connecting`` exists for one purpose:
#: prevent re-entrant ``connect()`` calls from racing the open handshake.
ConnectionState = Literal["closed", "connecting", "connected"]

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
        #: Tristate WS state. Mirrors TS ``SessionsWebSocket.ts:84``. Starts
        #: ``"closed"`` so a fresh instance is not mistaken for "connected".
        self._state: ConnectionState = "closed"
        #: Set True only by ``disconnect()`` (user-requested permanent
        #: close). Distinct from ``_state == "closed"`` which can also
        #: mean "between connections, waiting to reconnect". Once set,
        #: any reconnect-schedule path becomes a no-op until
        #: ``reconnect()`` clears it.
        self._user_disconnected = False
        self._reconnect_attempts = 0
        self._not_found_retries = 0

    # ─── State queries ────────────────────────────────────────────────

    def is_connected(self) -> bool:
        """True only after the WS open handshake has completed.

        Mirrors TS ``SessionsWebSocket.ts:362-364`` (``state === 'connected'``).
        Does NOT return True during the ``connecting`` handshake window.
        """
        return self._state == "connected"

    @property
    def state(self) -> ConnectionState:
        """Read-only view of the WS lifecycle state (testing + debugging)."""
        return self._state

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WS and start the reader. Returns once open OR raises.

        Single-flight: re-entrant calls during ``connecting`` or
        ``connected`` are no-ops. Mirrors TS guard at
        ``SessionsWebSocket.ts:101-104``. Permanent: returns immediately
        if ``disconnect()`` was called (use ``reconnect()`` to clear).
        """
        if self._state in ("connecting", "connected"):
            logger.debug(
                '[SessionsWebSocket] connect() called in state=%s; ignoring',
                self._state,
            )
            return
        if self._user_disconnected:
            logger.debug(
                '[SessionsWebSocket] connect() called after disconnect(); ignoring'
            )
            return
        # Transition: closed → connecting BEFORE the await so subsequent
        # callers see the in-flight state.
        self._state = "connecting"
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
            # Transition: connecting → closed on raise. Fire on_error,
            # then let the budgeted reconnect path decide whether to
            # retry.
            self._state = "closed"
            await self._invoke(self._callbacks.on_error, exc)
            self._schedule_reconnect_or_close()
            return

        # Post-await race: ``disconnect()`` may have run while we awaited
        # ``ws_connect``. If so, state is no longer "connecting" — close
        # the orphan WS and bail without starting the reader.
        if self._state != "connecting":
            logger.debug(
                '[SessionsWebSocket] disconnect() ran during handshake (state=%s); '
                'closing orphan WS',
                self._state,
            )
            try:
                await ws.close()
            except (websockets.exceptions.ConnectionClosed, OSError):
                pass
            return

        self._ws = ws
        self._state = "connected"
        self._reconnect_attempts = 0
        self._not_found_retries = 0
        await self._invoke(self._callbacks.on_connected)
        self._reader_task = asyncio.get_running_loop().create_task(
            self._read_loop(), name='sessions-ws-reader',
        )

    async def disconnect(self) -> None:
        """Permanent close — no reconnects, fire on_close on next close event.

        Re-entrant safe. Sets ``_user_disconnected`` so:
          - any in-flight ``connect()`` (mid ``ws_connect`` await) bails
            after returning, closing the orphan WS;
          - any scheduled-but-not-yet-fired reconnect callback no-ops;
          - subsequent ``connect()`` calls are rejected until
            ``reconnect()`` clears the latch.
        """
        was_quiescent = (
            self._state == "closed"
            and self._user_disconnected
            and self._ws is None
            and (self._reader_task is None or self._reader_task.done())
        )
        # Transition first so any in-flight connect() coroutine sees the
        # change after its ``await ws_connect()`` returns.
        self._state = "closed"
        # Mark as user-requested permanent close so any in-flight
        # reconnect scheduler (triggered by _on_ws_close just before
        # this call) or scheduled connect() callback bails out.
        self._user_disconnected = True
        # Always cancel any pending reconnect task — even when state was
        # already "closed", a budgeted reconnect may be sleeping and we
        # want it to never fire.
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if was_quiescent:
            return
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
        Transitions to ``closed`` before the grace sleep so the eventual
        ``connect()`` clears its singleflight guard. Also clears the
        ``_user_disconnected`` latch so a previously-disconnected
        instance can be re-armed (TS ``reconnect()`` mirrors the same
        intent at ``SessionsWebSocket.ts:393-403``).
        """
        self._reconnect_attempts = 0
        self._not_found_retries = 0
        self._user_disconnected = False
        # Move to closed so a subsequent connect() is not rejected by
        # the singleflight guard.
        self._state = "closed"
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
        """Send a ``control_response`` (e.g., permission decision).

        Mirrors TS ``SessionsWebSocket.ts:328-335`` — gated strictly on
        ``state === 'connected'`` so a mid-handshake send (state
        ``"connecting"``) is dropped, not transmitted.
        """
        if self._state != "connected" or self._ws is None:
            logger.warning(
                '[SessionsWebSocket] cannot send: state=%s', self._state
            )
            return
        try:
            await self._ws.send(json.dumps(response))
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            logger.warning('[SessionsWebSocket] send failed: %s', exc)

    async def send_control_request(self, inner: dict) -> None:
        """Wrap ``inner`` in a ``control_request`` envelope and send.

        State-gated identically to ``send_control_response``.
        """
        if self._state != "connected" or self._ws is None:
            logger.warning(
                '[SessionsWebSocket] cannot send: state=%s', self._state
            )
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

        Transitions ``connected → closed`` before evaluating the close
        code so any concurrent caller sees the closed state.
        """
        if self._state == "closed":
            return
        self._state = "closed"
        self._ws = None

        # If the user already disconnected, do nothing — the reader
        # itself was cancelled by disconnect(), so we're cleaning up
        # after a race the user already chose to lose.
        if self._user_disconnected:
            logger.debug(
                '[SessionsWebSocket] _on_ws_close after disconnect(); no reconnect'
            )
            return

        if close_code == WS_CLOSE_PERMANENT_UNAUTHORIZED:
            logger.debug(
                '[SessionsWebSocket] permanent close 4003; not reconnecting'
            )
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
            asyncio.get_running_loop().create_task(
                self._invoke(self._callbacks.on_close)
            )
            return
        self._schedule_reconnect_with_delay(RECONNECT_DELAY_SECONDS)

    def _schedule_reconnect_or_close(self) -> None:
        """Initial-connect failure path: same retry strategy as transient close.

        Called from ``connect()``'s ws_connect ``except`` branch (state
        was just set to ``"closed"``). Bails if the user has disconnected.
        """
        if self._user_disconnected:
            return
        if self._state != "closed":
            return
        self._reconnect_attempts += 1
        if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
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
            # If the user called disconnect() during the sleep, the
            # latch is set; bail. Also bail if state has moved away
            # from "closed" (e.g., a manual reconnect() ran).
            if self._user_disconnected or self._state != "closed":
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
    'ConnectionState',
    'GetAccessToken',
    'MAX_RECONNECT_ATTEMPTS',
    'MAX_SESSION_NOT_FOUND_RETRIES',
    'PING_INTERVAL_SECONDS',
    'RECONNECT_DELAY_SECONDS',
    'SessionsWebSocket',
    'SessionsWebSocketCallbacks',
]
