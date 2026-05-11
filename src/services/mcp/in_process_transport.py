"""In-process transport: paired client/server within the same Python process.

Mirrors typescript/src/services/mcp/InProcessTransport.ts (63 LOC). Used by
built-in MCP servers that ship as part of the host process — Chrome MCP and
Computer Use MCP in the TS port.

Design contract:
- ``send()`` delivers directly to the peer's inbox via ``put_nowait``. The
  ``asyncio.Queue`` already decouples producer and consumer, so no
  ``call_soon``/``queueMicrotask``-style indirection is needed. Direct
  delivery preserves the TS "send-then-close delivers the message before
  the close" contract — under a deferred ``call_soon`` scheme, ``close()``
  runs synchronously and pushes the sentinel ahead of the deferred
  put_nowait, silently dropping the message.
- ``close()`` cascades a sentinel to BOTH inboxes (own + peer) so any
  pending ``receive()`` unblocks immediately and returns ``None``.
- ``receive()`` drains pending messages before reporting close (otherwise
  the cascade would race the producer and lose buffered messages).

Callers MUST call ``close()`` explicitly.
"""

from __future__ import annotations

import asyncio
from typing import Union

from .transport import JsonRpcMessage, McpTransport


class _ClosedSentinel:
    """Singleton type pushed onto a transport's inbox when its peer closes.

    Dedicated type rather than a bare ``object()`` so static checkers can
    narrow the receive() return type and so future code can never confuse
    a user-supplied object with the close signal.
    """


_CLOSED_SENTINEL = _ClosedSentinel()
_InboxItem = Union[JsonRpcMessage, _ClosedSentinel]


class InProcessTransport(McpTransport):
    """Half of a linked transport pair. Paired at construction-time by
    ``create_linked_transport_pair()``; do not instantiate directly."""

    def __init__(self, peer: "InProcessTransport | None" = None) -> None:
        self._peer: InProcessTransport | None = peer
        self._inbox: asyncio.Queue[_InboxItem] = asyncio.Queue()
        self._closed = False

    def _set_peer(self, peer: "InProcessTransport") -> None:
        self._peer = peer

    async def start(self) -> None:
        # No-op: the linked pair is connected at factory time. Provided to
        # match the McpTransport ABC; calling start() multiple times is safe.
        return None

    async def send(self, message: JsonRpcMessage) -> None:
        if self._closed:
            raise RuntimeError("Transport is closed")
        if self._peer is None or self._peer._closed:
            raise RuntimeError("Peer not connected")
        # Direct delivery to the peer's inbox. The unbounded asyncio.Queue
        # decouples timing — the consumer awaits get() on its own task —
        # so no call_soon indirection is needed, and direct delivery
        # preserves the "messages sent before close are delivered"
        # ordering contract.
        self._peer._inbox.put_nowait(message)

    async def receive(self) -> JsonRpcMessage | None:
        # Drain pending messages even after close: the cascade-close path
        # marks ``_closed = True`` and THEN pushes the sentinel, so an
        # ``if self._closed`` short-circuit at the top of receive() would
        # silently drop any message buffered before the close. Only
        # return None upfront when the inbox is fully drained AND we're
        # closed (otherwise ``get()`` would block forever waiting for a
        # put that will never come).
        if self._closed and self._inbox.empty():
            return None
        item = await self._inbox.get()
        if isinstance(item, _ClosedSentinel):
            return None
        return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Push sentinel into our own inbox so any local pending receive()
        # unblocks. The asyncio.Queue is unbounded today, so put_nowait
        # cannot raise QueueFull.
        self._inbox.put_nowait(_CLOSED_SENTINEL)
        # Cascade close to the peer. Without this, a long-running receiver
        # on the peer side would never see the close signal and would hang.
        if self._peer is not None and not self._peer._closed:
            self._peer._closed = True
            self._peer._inbox.put_nowait(_CLOSED_SENTINEL)

    @property
    def is_connected(self) -> bool:
        return (
            not self._closed
            and self._peer is not None
            and not self._peer._closed
        )


def create_linked_transport_pair() -> tuple[InProcessTransport, InProcessTransport]:
    """Create a pair of bidirectionally linked in-process transports.

    Returns ``(a, b)`` where ``a.send(msg)`` delivers ``msg`` to
    ``b.receive()`` and vice versa. Closing either side cascades to the
    other.

    Mirrors ``createLinkedTransportPair()`` from
    typescript/src/services/mcp/InProcessTransport.ts.
    """
    a = InProcessTransport()
    b = InProcessTransport()
    a._set_peer(b)
    b._set_peer(a)
    return a, b
