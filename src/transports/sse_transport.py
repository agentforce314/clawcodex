"""SSE read transport for the CCR Bridge v2 stream.

Ports the read-side surface of
``typescript/src/transports/SSETransport.ts (711 lines)``. We port
the **functional surface** (connect, parse SSE frames, dispatch
events, reconnect with Last-Event-ID resume), not every TS line.

Key design decisions:

  - **`httpx-sse` does NOT auto-resume `Last-Event-ID`** on reconnect
    (Risk #21 in the refactoring plan). We track ``_last_event_id``
    ourselves and pass it as a header on every reconnect.
  - **Reconnect budget** matches TS: max 5 attempts with constant
    ``RECONNECT_DELAY_SECONDS = 2.0``. Exhaustion fires
    ``on_close(WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED)``.
  - **Backpressure**: ``on_data`` is called synchronously per event;
    the pump task awaits the next byte so a slow consumer naturally
    backpressures the read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from httpx_sse import aconnect_sse

from src.bridge.close_codes import WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED

logger = logging.getLogger(__name__)

DEFAULT_RECONNECT_DELAY_SECONDS = 2.0
DEFAULT_RECONNECT_BUDGET = 5

GetAuthHeaders = Callable[[], dict[str, str]]
OnData = Callable[[str], None]
OnClose = Callable[[int | None], None]
OnEvent = Callable[['SSEEvent'], None]


@dataclass
class SSEEvent:
    """One parsed SSE frame.

    ``event_id``: the SSE ``id:`` field value, used by the consumer to
    update the Last-Event-ID cursor that drives history-resume on
    reconnect.
    """

    event_id: str | None
    event: str | None
    data: str


class SSETransport:
    """Async SSE long-poll client with Last-Event-ID resume.

    Public API mirrors the surface ``ReplBridgeTransport`` consumes:

        connect()                   — open the stream (returns when first
                                       frame arrives or stream errors)
        set_on_data(cb)             — receive raw ``data:`` payloads
        set_on_event(cb)            — receive parsed SSEEvent objects
        set_on_close(cb)            — receive close-codes
        get_last_sequence_num()     — current Last-Event-ID cursor
        is_connected_status()       — True while a stream is open
        is_closed_status()          — True after permanent close
        close()                     — terminate the stream + reconnect loop
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        session_id: str | None = None,
        get_auth_headers: GetAuthHeaders | None = None,
        initial_sequence_num: int = 0,
        reconnect_delay_seconds: float = DEFAULT_RECONNECT_DELAY_SECONDS,
        reconnect_budget: int = DEFAULT_RECONNECT_BUDGET,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._session_id = session_id
        self._get_auth_headers = get_auth_headers
        self._last_event_id: str = str(initial_sequence_num) if initial_sequence_num else ''
        self._reconnect_delay = reconnect_delay_seconds
        self._reconnect_budget = reconnect_budget
        self._reconnect_attempts = 0

        self._client = client
        self._owned_client = client is None
        self._on_data: OnData | None = None
        self._on_close: OnClose | None = None
        self._on_event: OnEvent | None = None

        self._connected = False
        self._closed = False
        self._read_task: asyncio.Task[None] | None = None

    # ─── Public callback wiring ────────────────────────────────────────

    def set_on_data(self, cb: OnData) -> None:
        self._on_data = cb

    def set_on_close(self, cb: OnClose) -> None:
        self._on_close = cb

    def set_on_event(self, cb: OnEvent) -> None:
        self._on_event = cb

    # ─── Public state queries ─────────────────────────────────────────

    def is_connected_status(self) -> bool:
        return self._connected and not self._closed

    def is_closed_status(self) -> bool:
        return self._closed

    def get_last_sequence_num(self) -> int:
        """Current Last-Event-ID as int (0 if missing or non-numeric)."""
        try:
            return int(self._last_event_id) if self._last_event_id else 0
        except ValueError:
            return 0

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Spawn the read loop. Returns immediately; the caller awaits
        the first event via ``set_on_data`` / ``set_on_event``."""
        if self._read_task is not None and not self._read_task.done():
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=None)
        loop = asyncio.get_running_loop()
        self._read_task = loop.create_task(self._read_loop(), name='sse-read-loop')

    def close(self) -> None:
        """Terminate the read loop. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()

    async def aclose(self) -> None:
        """Cancel the loop AND await the underlying client cleanup."""
        self.close()
        if self._read_task is not None:
            try:
                await self._read_task
            except (asyncio.CancelledError, httpx.HTTPError):
                pass
        if self._owned_client and self._client is not None:
            await self._client.aclose()

    # ─── Read loop ────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Open the SSE stream, parse frames, fire callbacks; reconnect on drop."""
        assert self._client is not None
        while not self._closed:
            headers = dict(self._headers)
            if self._get_auth_headers is not None:
                headers.update(self._get_auth_headers())
            if self._last_event_id:
                # Per Risk #21: httpx-sse does NOT auto-resume
                # Last-Event-ID. Pass it explicitly on each (re)connect
                # so the server resumes from the right cursor.
                headers['Last-Event-ID'] = self._last_event_id

            try:
                async with aconnect_sse(
                    self._client,
                    'GET',
                    self._url,
                    headers=headers,
                ) as event_source:
                    self._connected = True
                    self._reconnect_attempts = 0
                    async for sse in event_source.aiter_sse():
                        if self._closed:
                            return
                        if sse.id:
                            self._last_event_id = sse.id
                        event = SSEEvent(
                            event_id=sse.id or None,
                            event=sse.event or None,
                            data=sse.data,
                        )
                        if self._on_event is not None:
                            try:
                                self._on_event(event)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning('[sse] on_event callback raised: %s', exc)
                        if self._on_data is not None:
                            try:
                                self._on_data(sse.data)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning('[sse] on_data callback raised: %s', exc)
            except asyncio.CancelledError:
                # close() cancellation — exit cleanly without a reconnect.
                self._connected = False
                return
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                logger.debug('[sse] stream error: %s', exc)
                self._connected = False
            except Exception as exc:  # noqa: BLE001 -- unknown errors from httpx-sse
                logger.warning('[sse] unexpected error: %s', exc)
                self._connected = False

            # Reconnect with budget.
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._reconnect_budget or self._closed:
                self._closed = True
                self._connected = False
                if self._on_close is not None:
                    try:
                        self._on_close(WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning('[sse] on_close callback raised: %s', exc)
                return

            try:
                await asyncio.sleep(self._reconnect_delay)
            except asyncio.CancelledError:
                self._connected = False
                return


__all__ = [
    'DEFAULT_RECONNECT_BUDGET',
    'DEFAULT_RECONNECT_DELAY_SECONDS',
    'GetAuthHeaders',
    'OnClose',
    'OnData',
    'OnEvent',
    'SSEEvent',
    'SSETransport',
]
