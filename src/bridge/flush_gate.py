"""State machine for gating message writes during an initial flush.

Ports ``typescript/src/bridge/flushGate.ts``.

When a bridge session starts, historical messages are flushed to the
server via a single HTTP POST. During that flush, new messages must
be queued to prevent them from arriving at the server interleaved with
the historical ones.

Lifecycle:
    start()      → enqueue() returns True; items are queued
    end()        → returns queued items for draining; enqueue() returns False
    drop()       → discards queued items (permanent transport close)
    deactivate() → clears active flag without dropping queued items
                    (transport replacement — the new transport will drain
                    the pending items)

Concurrency: single-coroutine use only — concurrent ``enqueue``/``end``
calls from different asyncio tasks are undefined behavior. Asyncio +
threads is a footgun; the TS source assumes a single-threaded event loop.
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar('T')


class FlushGate(Generic[T]):
    """Queue-with-lifecycle that gates writes during a one-shot flush."""

    __slots__ = ('_active', '_pending')

    def __init__(self) -> None:
        self._active: bool = False
        self._pending: list[T] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def start(self) -> None:
        """Mark flush as in-progress. ``enqueue`` will start queuing items."""
        self._active = True

    def end(self) -> list[T]:
        """End the flush and return queued items for draining.

        The caller is responsible for sending the returned items.
        Subsequent ``enqueue`` calls return False until ``start`` is called
        again.
        """
        self._active = False
        items = self._pending
        self._pending = []
        return items

    def enqueue(self, *items: T) -> bool:
        """If flush is active, queue ``items`` and return True.

        If flush is not active, return False (caller should send directly).
        """
        if not self._active:
            return False
        self._pending.extend(items)
        return True

    def drop(self) -> int:
        """Discard all queued items (permanent transport close).

        Returns the number of items dropped.
        """
        self._active = False
        count = len(self._pending)
        self._pending = []
        return count

    def deactivate(self) -> None:
        """Clear the active flag without dropping queued items.

        Used when the transport is replaced (``onWorkReceived``) — the new
        transport's flush will drain the pending items.
        """
        self._active = False


__all__ = ['FlushGate']
