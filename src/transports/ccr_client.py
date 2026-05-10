"""CCR Bridge v2 write transport (HTTP POST batching + heartbeat).

Ports the consumer-facing surface of
``typescript/src/transports/ccrClient.ts (998 lines)``.

Three responsibilities:

1. **`SerialBatchEventUploader`** — `asyncio.Queue(maxsize=N)` of
   pending events; a single uploader task drains the queue in batches
   of up to `max_batch_size` and POSTs to ``/worker/events``. Per
   Risk #23: queue blocks on the producer side (back-pressure to
   `write_event`); a configurable timeout triggers `dropped_batch_count`
   if the producer is starved (the queue is full and stays full).

2. **Heartbeat** — `asyncio.Task` that POSTs `/worker/heartbeat` every
   `heartbeat_interval_seconds` (default 20s with optional jitter).
   Cancelled on close.

3. **State / metadata / delivery** — `report_state`, `report_metadata`,
   `report_delivery` are best-effort POSTs.

**Epoch mismatch (409)** — when the server returns 409, the configured
`on_epoch_mismatch` callback is fired. The TS callback throws
`'epoch superseded'` to unwind the caller; in Python we raise
`EpochSupersededError` (defined in `src.bridge.exceptions`).

This implementation is the **functional surface** the
`ReplBridgeTransport` needs; rare error branches (multipart timeout,
specific 5xx retry budgets) are simplified.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.bridge.exceptions import EpochSupersededError

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 20.0
DEFAULT_MAX_BATCH_SIZE = 100
DEFAULT_QUEUE_MAX_SIZE = 100
DEFAULT_PRODUCER_TIMEOUT_SECONDS = 30.0
#: Number of additional retries after the first attempt fails before
#: the batch is dropped. Total attempts = 1 (initial) + this many.
#: Renamed from "max_consecutive_failures" for clarity — the option
#: name describes the retry count, not the failure count.
DEFAULT_MAX_RETRIES_PER_BATCH = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0


GetAuthHeaders = Callable[[], dict[str, str]]
OnEpochMismatch = Callable[[], None]


@dataclass
class CCRClientOptions:
    """Construction-time knobs for ``CCRClient``."""

    get_auth_headers: GetAuthHeaders | None = None
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    heartbeat_jitter_fraction: float = 0.0
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE
    queue_max_size: int = DEFAULT_QUEUE_MAX_SIZE
    producer_timeout_seconds: float = DEFAULT_PRODUCER_TIMEOUT_SECONDS
    max_retries_per_batch: int = DEFAULT_MAX_RETRIES_PER_BATCH
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    on_epoch_mismatch: OnEpochMismatch | None = None


class CCRClient:
    """Write-side transport for Bridge v2.

    Lifecycle:
        client = CCRClient(base_url, options, http_client=...)
        await client.initialize(epoch)
        await client.write_event(msg)            # -> queue
        client.report_state({'status': '...'})    # fire-and-forget
        client.report_delivery(eid, 'received')   # fire-and-forget
        await client.flush()                      # drain the queue
        client.close()
    """

    def __init__(
        self,
        base_url: str,
        options: CCRClientOptions | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._options = options or CCRClientOptions()
        self._http = http_client
        self._owned_http = http_client is None

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._options.queue_max_size
        )
        self._epoch: int | None = None
        self._initialized = False
        self._closed = False

        self._uploader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

        #: Monotonic count of batches the uploader silently dropped due
        #: to repeated POST failures. Producers can snapshot this around
        #: ``write_event`` to detect drops (the future resolves normally
        #: even when the batch was lost).
        self._dropped_batch_count = 0

    # ─── Public properties ────────────────────────────────────────────

    @property
    def dropped_batch_count(self) -> int:
        return self._dropped_batch_count

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def initialize(self, epoch: int) -> None:
        """Set the worker epoch and spawn the uploader + heartbeat tasks."""
        if self._initialized:
            return
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        self._epoch = epoch
        self._initialized = True
        loop = asyncio.get_running_loop()
        self._uploader_task = loop.create_task(self._uploader_loop(), name='ccr-uploader')
        if self._options.heartbeat_interval_seconds > 0:
            self._heartbeat_task = loop.create_task(self._heartbeat_loop(), name='ccr-heartbeat')

    def close(self) -> None:
        """Cancel uploader + heartbeat, drop pending writes."""
        if self._closed:
            return
        self._closed = True
        for task in (self._uploader_task, self._heartbeat_task):
            if task is not None and not task.done():
                task.cancel()

    async def aclose(self) -> None:
        """Cancel everything and clean up the owned HTTP client."""
        self.close()
        for task in (self._uploader_task, self._heartbeat_task):
            if task is None:
                continue
            try:
                await task
            except (asyncio.CancelledError, httpx.HTTPError):
                pass
        if self._owned_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def flush(self) -> None:
        """Wait for the queue to drain.

        Note: only flushes events currently queued; events written after
        the call returns are not guaranteed to land before subsequent
        calls. Callers serialize their writes before flushing if they
        need a hard barrier.
        """
        await self._queue.join()

    # ─── Write API ────────────────────────────────────────────────────

    async def write_event(self, message: dict[str, Any]) -> None:
        """Enqueue ``message`` for batched POST to ``/worker/events``.

        Blocks if the queue is full (back-pressure to caller). Times out
        after ``producer_timeout_seconds``; on timeout, increments
        ``dropped_batch_count`` and returns (the message is lost).
        """
        if self._closed or not self._initialized:
            self._dropped_batch_count += 1
            return
        try:
            await asyncio.wait_for(
                self._queue.put(message),
                timeout=self._options.producer_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._dropped_batch_count += 1

    # ─── State / metadata / delivery (fire-and-forget POSTs) ──────────

    def report_state(self, state: dict[str, Any]) -> None:
        """PUT ``/worker`` state (e.g. ``{requires_action: True}``)."""
        if self._closed:
            return
        asyncio.get_running_loop().create_task(
            self._safe_put('/worker', json={'state': state}),
            name='ccr-report-state',
        )

    def report_metadata(self, metadata: dict[str, Any]) -> None:
        if self._closed:
            return
        asyncio.get_running_loop().create_task(
            self._safe_put('/worker', json={'external_metadata': metadata}),
            name='ccr-report-metadata',
        )

    def report_delivery(self, event_id: str, status: str) -> None:
        """POST ``/worker/events/{event_id}/delivery``."""
        if self._closed:
            return
        path = f'/worker/events/{event_id}/delivery'
        asyncio.get_running_loop().create_task(
            self._safe_post(path, json={'status': status}),
            name='ccr-report-delivery',
        )

    # ─── Internal: HTTP helpers ───────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {'Content-Type': 'application/json'}
        if self._options.get_auth_headers is not None:
            h.update(self._options.get_auth_headers())
        if self._epoch is not None:
            h['X-Worker-Epoch'] = str(self._epoch)
        return h

    async def _safe_post(self, path: str, json: dict[str, Any]) -> None:
        if self._http is None:
            return
        url = f'{self._base_url}{path}'
        try:
            resp = await self._http.post(url, json=json, headers=self._headers())
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug('[ccr] POST %s failed: %s', path, exc)
            return
        await self._handle_response(resp)

    async def _safe_put(self, path: str, json: dict[str, Any]) -> None:
        if self._http is None:
            return
        url = f'{self._base_url}{path}'
        try:
            resp = await self._http.put(url, json=json, headers=self._headers())
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug('[ccr] PUT %s failed: %s', path, exc)
            return
        await self._handle_response(resp)

    async def _handle_response(self, resp: httpx.Response) -> None:
        if resp.status_code == 409:
            logger.debug('[ccr] 409 epoch superseded')
            cb = self._options.on_epoch_mismatch
            if cb is not None:
                try:
                    cb()
                except EpochSupersededError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug('[ccr] on_epoch_mismatch callback raised: %s', exc)
            raise EpochSupersededError(
                'CCR worker epoch superseded (409)'
            )
        if resp.status_code >= 500:
            logger.debug('[ccr] server error %d', resp.status_code)

    # ─── Internal: uploader + heartbeat loops ─────────────────────────

    async def _uploader_loop(self) -> None:
        """Drain the queue in batches; POST each batch to /worker/events.

        On failure, re-attempt the same batch up to
        ``max_retries_per_batch`` times before dropping (matches TS
        ``maxConsecutiveFailures`` semantics — failures count for one
        batch, not lifetime). Drops increment ``dropped_batch_count``.
        """
        while not self._closed:
            try:
                first = await self._queue.get()
            except asyncio.CancelledError:
                return
            batch: list[dict[str, Any]] = [first]
            for _ in range(self._options.max_batch_size - 1):
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            # Mark queue items as done immediately — we own the batch
            # now; retries don't go back through the queue.
            for _ in batch:
                self._queue.task_done()

            failures = 0
            while not self._closed:
                try:
                    success = await self._post_batch(batch)
                except EpochSupersededError:
                    # Don't keep retrying through epoch mismatches —
                    # the parent transport will close us.
                    return
                if success:
                    break
                failures += 1
                if failures > self._options.max_retries_per_batch:
                    # Total attempts = 1 (initial) + max_retries_per_batch.
                    # Now exhausted; drop the batch and proceed.
                    self._dropped_batch_count += 1
                    break
                try:
                    await asyncio.sleep(self._options.retry_backoff_seconds)
                except asyncio.CancelledError:
                    return

    async def _post_batch(self, batch: list[dict[str, Any]]) -> bool:
        """POST one batch. Returns True on success, False on transient failure.

        Raises ``EpochSupersededError`` on 409 — caller must let it
        propagate so the parent transport can recreate.
        """
        if self._http is None:
            return False
        url = f'{self._base_url}/worker/events'
        try:
            resp = await self._http.post(
                url,
                json={'events': batch},
                headers=self._headers(),
            )
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug('[ccr] batch POST failed: %s', exc)
            return False
        if resp.status_code == 409:
            logger.debug('[ccr] batch POST: 409 epoch superseded')
            cb = self._options.on_epoch_mismatch
            if cb is not None:
                try:
                    cb()
                except EpochSupersededError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug('[ccr] on_epoch_mismatch callback raised: %s', exc)
            raise EpochSupersededError('CCR worker epoch superseded (409)')
        if resp.status_code >= 500:
            logger.debug('[ccr] batch POST 5xx: %d', resp.status_code)
            return False
        return True

    async def _heartbeat_loop(self) -> None:
        """Periodic POST ``/worker/heartbeat`` with optional jitter."""
        interval = self._options.heartbeat_interval_seconds
        jitter = self._options.heartbeat_jitter_fraction
        while not self._closed:
            try:
                # ±jitter fraction of the interval.
                delta = random.uniform(-jitter, jitter) * interval if jitter > 0 else 0.0
                await asyncio.sleep(max(0.1, interval + delta))
            except asyncio.CancelledError:
                return
            await self._safe_post('/worker/heartbeat', json={})


__all__ = [
    'CCRClient',
    'CCRClientOptions',
    'DEFAULT_HEARTBEAT_INTERVAL_SECONDS',
    'DEFAULT_MAX_BATCH_SIZE',
    'DEFAULT_MAX_RETRIES_PER_BATCH',
    'DEFAULT_PRODUCER_TIMEOUT_SECONDS',
    'DEFAULT_QUEUE_MAX_SIZE',
    'DEFAULT_RETRY_BACKOFF_SECONDS',
    'GetAuthHeaders',
    'OnEpochMismatch',
]
