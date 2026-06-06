"""Hybrid transport — WebSocket reads + HTTP POST writes.

Ports ``typescript/src/cli/transports/HybridTransport.ts``.

Design overview
---------------

Subclasses :class:`WebSocketTransport` (the reconnecting WS client
from phase 14a). The read side is fully inherited — incoming frames
fire ``on_data`` via the parent's reader loop. The write side is
overridden to POST through :class:`SerialBatchEventUploader`
(phase 14b) instead of using the inherited WS ``send``.

Write flow::

    write(stream_event) ─┐
                         │ (100ms timer)
                         ▼
    write(other) ────► uploader.enqueue()  (SerialBatchEventUploader)
                         ▲    │
    write_batch() ───────┘    │ serial, batched, retries with backoff,
                              │ backpressure at max_queue_size.
                              ▼
                         _post_once()  (single httpx POST; raises on retryable)

``stream_event`` messages accumulate in ``_stream_event_buffer`` for
up to ``BATCH_FLUSH_INTERVAL_S`` (100ms) before being enqueued — this
reduces POST count for high-volume content deltas. A non-stream
write flushes any buffered stream events first to preserve order.

Why serialize?
~~~~~~~~~~~~~~

Bridge mode fires writes via fire-and-forget (``loop.create_task(
transport.write(...))``). Without serialization, concurrent POSTs to
the same session-ingress endpoint would race in Firestore on the
server side, producing retry storms and oncall pages.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from src.transports.serial_batch_event_uploader import (
    SerialBatchEventUploader,
    SerialBatchEventUploaderConfig,
)
from src.transports.websocket_transport import (
    WebSocketTransport,
    WebSocketTransportOptions,
)
from src.utils.session_ingress_auth import get_session_ingress_auth_token

logger = logging.getLogger(__name__)


#: Delay before flushing accumulated ``stream_event`` messages. Mirrors
#: TS ``BATCH_FLUSH_INTERVAL_MS``.
BATCH_FLUSH_INTERVAL_S = 0.1

#: Per-attempt POST timeout. Bounds how long a single stuck request
#: can block the serialized queue. Mirrors TS ``POST_TIMEOUT_MS``.
POST_TIMEOUT_S = 15.0

#: Grace period for queued writes on ``close()``. Best-effort; not a
#: delivery guarantee under degraded network. Mirrors TS ``CLOSE_GRACE_MS``.
CLOSE_GRACE_S = 3.0

# Uploader knobs — mirror TS hardcoded values in ``HybridTransport.ts:76-92``.
_MAX_BATCH_SIZE = 500
_MAX_QUEUE_SIZE = 100_000
_BASE_DELAY_MS = 500.0
_MAX_DELAY_MS = 8000.0
_JITTER_MS = 1000.0


class HybridTransport(WebSocketTransport):
    """v1 transport — WS reads (inherited) + HTTP POST writes.

    Use this when the work secret indicates a v1 session-ingress
    endpoint (``use_code_sessions=False``). The CCR-v2 path uses
    SSE + CCRClient via ``src.transports.sse_transport`` and
    ``src.transports.ccr_client`` instead.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        session_id: str | None = None,
        refresh_headers: Callable[[], dict[str, str]] | None = None,
        options: WebSocketTransportOptions | None = None,
        *,
        max_consecutive_failures: int | None = None,
        on_batch_dropped: Callable[[int, int], None] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            url, headers, session_id, refresh_headers, options,
        )
        self._post_url = _convert_ws_url_to_post_url(url)
        if http_client is not None:
            self._http_client = http_client
            self._owns_http_client = False
        else:
            self._http_client = httpx.AsyncClient(timeout=POST_TIMEOUT_S)
            self._owns_http_client = True
        self._stream_event_buffer: list[dict[str, Any]] = []
        self._stream_event_timer: asyncio.TimerHandle | None = None
        self._uploader: SerialBatchEventUploader[dict[str, Any]] = (
            SerialBatchEventUploader(
                SerialBatchEventUploaderConfig(
                    max_batch_size=_MAX_BATCH_SIZE,
                    max_queue_size=_MAX_QUEUE_SIZE,
                    base_delay_ms=_BASE_DELAY_MS,
                    max_delay_ms=_MAX_DELAY_MS,
                    jitter_ms=_JITTER_MS,
                    max_consecutive_failures=max_consecutive_failures,
                    on_batch_dropped=on_batch_dropped,
                    send=self._post_once,
                )
            )
        )
        logger.debug('HybridTransport: POST URL = %s', self._post_url)

    @property
    def dropped_batch_count(self) -> int:
        return self._uploader.dropped_batch_count

    # ─── Write API (overrides parent's WS-based ``write``) ───────────

    async def write(self, message: dict[str, Any]) -> None:
        """Enqueue a message for POST. Returns after the bytes are
        accepted by the uploader (which then drains serially)."""
        if message.get('type') == 'stream_event':
            self._stream_event_buffer.append(message)
            if self._stream_event_timer is None:
                loop = asyncio.get_running_loop()
                self._stream_event_timer = loop.call_later(
                    BATCH_FLUSH_INTERVAL_S,
                    self._on_stream_event_timer_fire,
                )
            return
        buffered = self._take_stream_events()
        await self._uploader.enqueue(buffered + [message])
        await self._uploader.flush()

    async def write_batch(self, messages: list[dict[str, Any]]) -> None:
        buffered = self._take_stream_events()
        await self._uploader.enqueue(buffered + list(messages))
        await self._uploader.flush()

    async def flush(self) -> None:
        buffered = self._take_stream_events()
        if buffered:
            await self._uploader.enqueue(buffered)
        await self._uploader.flush()

    def close(self) -> None:
        if self._stream_event_timer is not None:
            self._stream_event_timer.cancel()
            self._stream_event_timer = None
        self._stream_event_buffer = []
        uploader = self._uploader
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._grace_close_uploader(uploader),
                name='hybrid-transport-close-grace',
            )
        except RuntimeError:
            uploader.close()
        super().close()

    # ─── Internal ────────────────────────────────────────────────────

    def _take_stream_events(self) -> list[dict[str, Any]]:
        if self._stream_event_timer is not None:
            self._stream_event_timer.cancel()
            self._stream_event_timer = None
        buffered, self._stream_event_buffer = self._stream_event_buffer, []
        return buffered

    def _on_stream_event_timer_fire(self) -> None:
        self._stream_event_timer = None
        buffered, self._stream_event_buffer = self._stream_event_buffer, []
        if not buffered:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._uploader.enqueue(buffered),
                name='hybrid-transport-stream-flush',
            )
        except RuntimeError:
            logger.warning(
                'HybridTransport: no running loop in stream-timer fire; '
                'dropping %d stream events', len(buffered),
            )

    async def _grace_close_uploader(
        self, uploader: SerialBatchEventUploader[dict[str, Any]],
    ) -> None:
        try:
            await asyncio.wait_for(uploader.flush(), timeout=CLOSE_GRACE_S)
        except asyncio.TimeoutError:
            logger.debug(
                'HybridTransport: close grace expired with pending writes',
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug('HybridTransport: grace flush error: %s', exc)
        finally:
            uploader.close()
            if self._owns_http_client:
                try:
                    await self._http_client.aclose()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        'HybridTransport: http_client aclose error: %s',
                        exc,
                    )

    async def _post_once(self, events: list[dict[str, Any]]) -> None:
        session_token = get_session_ingress_auth_token()
        if not session_token:
            logger.debug(
                'HybridTransport: no session token available for POST',
            )
            return

        headers = {
            'Authorization': f'Bearer {session_token}',
            'Content-Type': 'application/json',
        }

        try:
            response = await self._http_client.post(
                self._post_url,
                json={'events': events},
                headers=headers,
            )
        except httpx.RequestError as exc:
            logger.debug(
                'HybridTransport: POST network error: %s', exc,
            )
            raise

        if 200 <= response.status_code < 300:
            logger.debug(
                'HybridTransport: POST success count=%d', len(events),
            )
            return
        if 400 <= response.status_code < 500 and response.status_code != 429:
            logger.warning(
                'HybridTransport: POST returned %d (permanent); '
                'dropping %d events', response.status_code, len(events),
            )
            return
        logger.warning(
            'HybridTransport: POST returned %d (retryable)',
            response.status_code,
        )
        raise RuntimeError(f'POST failed with {response.status_code}')


def _convert_ws_url_to_post_url(ws_url: str) -> str:
    """Convert a WebSocket URL to the matching HTTP POST endpoint.

    From: ``wss://api.example.com/v2/session_ingress/ws/<session_id>``
    To:   ``https://api.example.com/v2/session_ingress/session/<session_id>/events``
    """
    parsed = urlparse(ws_url)
    if parsed.scheme == 'wss':
        scheme = 'https'
    elif parsed.scheme == 'ws':
        scheme = 'http'
    else:
        scheme = parsed.scheme

    path = parsed.path.replace('/ws/', '/session/', 1)
    if not path.endswith('/events'):
        path = (path + 'events') if path.endswith('/') else (path + '/events')

    return urlunparse((
        scheme, parsed.netloc, path, parsed.params,
        parsed.query, parsed.fragment,
    ))


__all__ = [
    'BATCH_FLUSH_INTERVAL_S',
    'CLOSE_GRACE_S',
    'HybridTransport',
    'POST_TIMEOUT_S',
]