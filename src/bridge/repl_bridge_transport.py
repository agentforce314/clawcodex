"""``ReplBridgeTransport`` — unified v1/v2 transport interface.

Ports the consumer-facing surface of
``typescript/src/bridge/replBridgeTransport.ts:23-369``.

Two halves:

  * ``ReplBridgeTransport`` Protocol — the 14-method contract that
    ``replBridge`` writes against. v1 and v2 implementations conform.
  * ``create_v2_repl_transport`` — the v2 factory: SSE reads +
    ``CCRClient`` writes, with the epoch-mismatch handler that closes
    both transports + raises ``EpochSupersededError`` to unwind callers.

The v1 factory is **not yet implemented** (out of Phase 6 scope).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.bridge.close_codes import (
    WS_CLOSE_EPOCH_MISMATCH,
    WS_CLOSE_INIT_FAILURE,
)
from src.bridge.exceptions import EpochSupersededError
from src.transports.ccr_client import CCRClient, CCRClientOptions
from src.transports.hybrid_transport import HybridTransport
from src.transports.sse_transport import SSEEvent, SSETransport

logger = logging.getLogger(__name__)


# ─── Protocol ──────────────────────────────────────────────────────────────


class ReplBridgeTransport(Protocol):
    """The 14-method contract ``replBridge`` writes against.

    Mirrors TS ``replBridgeTransport.ts:23-70``. ``write`` and
    ``write_batch`` return awaitables (``async def`` in
    implementations); the rest are sync — flag-checks, callback
    registrations, etc.
    """

    async def write(self, message: dict[str, Any]) -> None: ...
    async def write_batch(self, messages: list[dict[str, Any]]) -> None: ...
    def close(self) -> None: ...
    def is_connected_status(self) -> bool: ...
    def get_state_label(self) -> str: ...
    def set_on_data(self, cb: Callable[[str], None]) -> None: ...
    def set_on_close(self, cb: Callable[[int | None], None]) -> None: ...
    def set_on_connect(self, cb: Callable[[], None]) -> None: ...
    def connect(self) -> None: ...
    def get_last_sequence_num(self) -> int: ...

    @property
    def dropped_batch_count(self) -> int: ...

    def report_state(self, state: dict[str, Any]) -> None: ...
    def report_metadata(self, metadata: dict[str, Any]) -> None: ...
    def report_delivery(self, event_id: str, status: str) -> None: ...
    async def flush(self) -> None: ...


# ─── v2 implementation ────────────────────────────────────────────────────


@dataclass
class V2TransportOptions:
    """Construction-time knobs for ``create_v2_repl_transport``."""

    session_url: str
    ingress_token: str
    session_id: str
    epoch: int
    initial_sequence_num: int = 0
    heartbeat_interval_seconds: float | None = None
    heartbeat_jitter_fraction: float | None = None
    outbound_only: bool = False
    get_auth_token: Callable[[], str | None] | None = None


class _V2ReplTransport:
    """Concrete v2 transport (SSE reads + CCRClient writes).

    Mirrors ``replBridgeTransport.ts:119-369``. Constructed via
    ``create_v2_repl_transport``; satisfies the ``ReplBridgeTransport``
    Protocol structurally.
    """

    def __init__(
        self,
        sse: SSETransport,
        ccr: CCRClient,
        *,
        epoch: int,
        outbound_only: bool,
    ) -> None:
        self._sse = sse
        self._ccr = ccr
        self._epoch = epoch
        self._outbound_only = outbound_only
        self._closed = False

        self._on_connect_cb: Callable[[], None] | None = None
        self._on_close_cb: Callable[[int | None], None] | None = None
        self._ccr_initialized = False

    # ─── State ──────────────────────────────────────────────────────────

    def is_connected_status(self) -> bool:
        """Write-readiness, not read-readiness — replBridge checks this
        before calling ``write_batch``. SSE open state is orthogonal."""
        return self._ccr_initialized and not self._closed

    def get_state_label(self) -> str:
        if self._sse.is_closed_status():
            return 'closed'
        if self._sse.is_connected_status():
            return 'connected' if self._ccr_initialized else 'init'
        return 'connecting'

    def get_last_sequence_num(self) -> int:
        if self._outbound_only:
            return 0  # no SSE read stream → no cursor to report
        return self._sse.get_last_sequence_num()

    @property
    def dropped_batch_count(self) -> int:
        return self._ccr.dropped_batch_count

    # ─── Callback wiring ───────────────────────────────────────────────

    def set_on_data(self, cb: Callable[[str], None]) -> None:
        if self._outbound_only:
            return  # no inbound stream — set_on_data is a no-op
        self._sse.set_on_data(cb)

    def set_on_close(self, cb: Callable[[int | None], None]) -> None:
        self._on_close_cb = cb
        # When the SSE reconnect-budget is exhausted, fire onClose so
        # replBridge can reconnect via the poll loop.
        self._sse.set_on_close(lambda code: cb(code))

    def set_on_connect(self, cb: Callable[[], None]) -> None:
        self._on_connect_cb = cb

    # ─── Lifecycle ─────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open SSE + initialize CCRClient. Returns immediately."""
        loop = asyncio.get_running_loop()
        loop.create_task(self._do_connect(), name='v2-transport-connect')

    async def _do_connect(self) -> None:
        if not self._outbound_only:
            await self._sse.connect()
        try:
            await self._ccr.initialize(self._epoch)
        except EpochSupersededError:
            self._fire_close(WS_CLOSE_EPOCH_MISMATCH)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning('[v2-transport] CCR init failed: %s', exc)
            self._fire_close(WS_CLOSE_INIT_FAILURE)
            return
        self._ccr_initialized = True
        if self._on_connect_cb is not None:
            try:
                self._on_connect_cb()
            except Exception as exc:  # noqa: BLE001
                logger.warning('[v2-transport] on_connect_cb raised: %s', exc)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._sse.close()
        self._ccr.close()

    async def aclose(self) -> None:
        self.close()
        await asyncio.gather(
            self._sse.aclose(),
            self._ccr.aclose(),
            return_exceptions=True,
        )

    # ─── Write API ─────────────────────────────────────────────────────

    async def write(self, message: dict[str, Any]) -> None:
        await self._ccr.write_event(message)

    async def write_batch(self, messages: list[dict[str, Any]]) -> None:
        for m in messages:
            if self._closed:
                return
            await self._ccr.write_event(m)

    async def flush(self) -> None:
        await self._ccr.flush()

    # ─── State / metadata / delivery passthrough ──────────────────────

    def report_state(self, state: dict[str, Any]) -> None:
        self._ccr.report_state(state)

    def report_metadata(self, metadata: dict[str, Any]) -> None:
        self._ccr.report_metadata(metadata)

    def report_delivery(self, event_id: str, status: str) -> None:
        self._ccr.report_delivery(event_id, status)

    # ─── Internal helpers ─────────────────────────────────────────────

    def _fire_close(self, code: int) -> None:
        cb = self._on_close_cb
        if cb is None:
            return
        try:
            cb(code)
        except Exception as exc:  # noqa: BLE001
            logger.warning('[v2-transport] on_close_cb raised: %s', exc)


# ─── Public factory ───────────────────────────────────────────────────────


async def create_v2_repl_transport(opts: V2TransportOptions) -> _V2ReplTransport:
    """Build a v2 transport (SSE reads + CCRClient writes).

    Mirrors ``replBridgeTransport.ts:119-369``. The
    ``on_epoch_mismatch`` handler closes both transports, fires
    ``on_close_cb(4090)``, and raises ``EpochSupersededError`` to
    unwind callers (matches TS ``throw 'epoch superseded'``).

    The auth-header closure is per-instance so multiple concurrent
    sessions don't clobber each other (mirrors the TS multi-session
    safety fix).
    """
    # Per-instance auth header closure (multi-session safety).
    def _auth_headers() -> dict[str, str]:
        token: str | None
        if opts.get_auth_token is not None:
            token = opts.get_auth_token()
        else:
            token = opts.ingress_token
        if not token:
            return {}
        return {'Authorization': f'Bearer {token}'}

    # Derive SSE stream URL: append /worker/events/stream to the session URL.
    sse_url = opts.session_url.rstrip('/') + '/worker/events/stream'
    sse = SSETransport(
        url=sse_url,
        headers={},
        session_id=opts.session_id,
        get_auth_headers=_auth_headers,
        initial_sequence_num=opts.initial_sequence_num,
    )

    transport_ref: dict[str, _V2ReplTransport | None] = {'t': None}

    def _on_epoch_mismatch() -> None:
        """Called by CCRClient on 409. Closes both, fires onClose(4090),
        raises EpochSupersededError to unwind the caller."""
        t = transport_ref['t']
        if t is not None:
            try:
                t._sse.close()
                t._ccr.close()
                t._fire_close(WS_CLOSE_EPOCH_MISMATCH)
            except Exception as exc:  # noqa: BLE001
                logger.warning('[v2-transport] epoch-mismatch cleanup: %s', exc)

    # Heartbeat options: pass through if explicitly set.
    ccr_opts_kwargs: dict[str, Any] = {
        'get_auth_headers': _auth_headers,
        'on_epoch_mismatch': _on_epoch_mismatch,
    }
    if opts.heartbeat_interval_seconds is not None:
        ccr_opts_kwargs['heartbeat_interval_seconds'] = opts.heartbeat_interval_seconds
    if opts.heartbeat_jitter_fraction is not None:
        ccr_opts_kwargs['heartbeat_jitter_fraction'] = opts.heartbeat_jitter_fraction
    ccr = CCRClient(
        base_url=opts.session_url,
        options=CCRClientOptions(**ccr_opts_kwargs),
    )

    # Wire SSE → CCRClient delivery ACKs (TS replBridgeTransport.ts:249-252).
    # ACK both 'received' AND 'processed' immediately so daemon-path
    # reconnects don't re-queue events forever.
    def _ack(event: SSEEvent) -> None:
        if not event.event_id:
            return
        try:
            ccr.report_delivery(event.event_id, 'received')
            ccr.report_delivery(event.event_id, 'processed')
        except Exception as exc:  # noqa: BLE001
            logger.debug('[v2-transport] delivery ACK failed: %s', exc)

    sse.set_on_event(_ack)

    transport = _V2ReplTransport(
        sse=sse,
        ccr=ccr,
        epoch=opts.epoch,
        outbound_only=opts.outbound_only,
    )
    transport_ref['t'] = transport
    return transport


class _V1ReplTransport:
    """v1 adapter — wraps :class:`HybridTransport` into the
    :class:`ReplBridgeTransport` Protocol surface.

    ``HybridTransport`` already has the full write/read API (it
    extends ``WebSocketTransport``); this adapter is a thin
    pass-through so the consumer's ``transport`` variable has a
    single type regardless of v1/v2.
    """

    def __init__(self, hybrid: 'HybridTransport') -> None:
        self._hybrid = hybrid

    async def write(self, message: dict[str, Any]) -> None:
        await self._hybrid.write(message)

    async def write_batch(self, messages: list[dict[str, Any]]) -> None:
        await self._hybrid.write_batch(messages)

    def close(self) -> None:
        self._hybrid.close()

    def is_connected_status(self) -> bool:
        return self._hybrid.is_connected_status()

    def get_state_label(self) -> str:
        return self._hybrid.get_state_label()

    def set_on_data(self, cb: Callable[[str], None]) -> None:
        self._hybrid.set_on_data(cb)

    def set_on_close(self, cb: Callable[[int | None], None]) -> None:
        self._hybrid.set_on_close(cb)

    def set_on_connect(self, cb: Callable[[], None]) -> None:
        self._hybrid.set_on_connect(cb)

    def connect(self) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(
            self._hybrid.connect(), name='v1-repl-transport-connect',
        )

    def get_last_sequence_num(self) -> int:
        return 0

    @property
    def dropped_batch_count(self) -> int:
        return self._hybrid.dropped_batch_count

    def report_state(self, state: dict[str, Any]) -> None:
        return None

    def report_metadata(self, metadata: dict[str, Any]) -> None:
        return None

    def report_delivery(self, event_id: str, status: str) -> None:
        return None

    async def flush(self) -> None:
        await self._hybrid.flush()


def create_v1_repl_transport(hybrid: 'HybridTransport') -> ReplBridgeTransport:
    return _V1ReplTransport(hybrid)


__all__ = [
    'ReplBridgeTransport',
    'V2TransportOptions',
    'create_v1_repl_transport',
    'create_v2_repl_transport',
]
