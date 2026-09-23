"""Pending-notification queue for parent-agent injection — Chunk D / WI-3.1.

Mirrors the ``messageQueueManager`` surface in
``typescript/src/utils/messageQueueManager.ts``. Notifications enqueued
here are drained at the next tool-round boundary (or by an explicit
caller) and surfaced as user-role messages in the parent agent's
conversation. The chapter calls these out as the "task notifications"
channel — the parent sees them in its normal message flow without a
special tool to poll.

The queue is process-global (one per Python process) for parity with
the TS module-singleton shape. A single ``threading.RLock`` guards the
deque; reads and writes are short.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Iterator, Literal

# Notification "mode" — the TS messageQueueManager carries a discriminator
# so different injection sites (task-notification vs other system
# messages) can be filtered. We adopt the same shape so future modes
# (e.g. permission-request escalations in Phase 9) can join the queue
# without reworking the contract.
NotificationMode = Literal["task-notification", "teammate-message"]


@dataclass(frozen=True)
class PendingNotification:
    """One queued notification awaiting injection into the parent's
    conversation. ``value`` is the literal user-role message text
    (chapter-shaped XML for the ``"task-notification"`` mode)."""

    value: str
    mode: NotificationMode = "task-notification"
    scope: object | None = None
    recipient: str | None = None


_lock = threading.RLock()
_queue: deque[PendingNotification] = deque()
_ALL_SCOPES = object()
_ALL_RECIPIENTS = object()


def enqueue_pending_notification(
    *,
    value: str,
    mode: NotificationMode = "task-notification",
    scope: object | None = None,
    recipient: str | None = None,
) -> None:
    """Push a notification onto the global queue.

    Mirrors TS ``enqueuePendingNotification``. Idempotency is the
    caller's responsibility — chapter-10 / WI-3.2's ``notified``
    check-and-set is what guards against duplicate XML; this queue
    is a dumb FIFO.
    """
    with _lock:
        _queue.append(
            PendingNotification(
                value=value, mode=mode, scope=scope, recipient=recipient
            )
        )


def drain_pending_notifications(
    *,
    mode: NotificationMode | None = None,
    scope: object = _ALL_SCOPES,
    recipient: object = _ALL_RECIPIENTS,
    active_recipients: set[str] | None = None,
) -> list[PendingNotification]:
    """Atomically pop every queued notification (or every notification
    of one ``mode``) and return them in FIFO order.

    Pass ``mode=None`` to drain everything, or a specific mode to drain
    only that subset (the others stay queued). The return value is a
    plain list — callers iterating outside the lock cannot see
    in-flight enqueues. Production consumers must pass their session registry
    as ``scope``; omitting it is an administrative drain of every session.
    """
    with _lock:
        if mode is None and scope is _ALL_SCOPES and recipient is _ALL_RECIPIENTS:
            drained = list(_queue)
            _queue.clear()
            return drained
        kept: deque[PendingNotification] = deque()
        drained_subset: list[PendingNotification] = []
        for entry in _queue:
            if (
                (mode is None or entry.mode == mode)
                and (scope is _ALL_SCOPES or entry.scope is scope)
                and (
                    recipient is _ALL_RECIPIENTS
                    or entry.recipient == recipient
                    or (
                        recipient is None
                        and active_recipients is not None
                        and entry.recipient not in active_recipients
                    )
                )
            ):
                drained_subset.append(entry)
            else:
                kept.append(entry)
        _queue.clear()
        _queue.extend(kept)
        return drained_subset


def peek_pending_notifications() -> list[PendingNotification]:
    """Snapshot of the queue without modifying it. Test/diagnostic use."""
    with _lock:
        return list(_queue)


def clear_pending_notifications() -> None:
    """Test helper — empty the queue. Production code should not need
    this; the drain path is the contract."""
    with _lock:
        _queue.clear()


def _queue_size() -> int:
    """Test helper — current queue depth."""
    with _lock:
        return len(_queue)


def __iter__() -> Iterator[PendingNotification]:  # pragma: no cover (module-level)
    return iter(peek_pending_notifications())


__all__ = [
    "PendingNotification",
    "NotificationMode",
    "enqueue_pending_notification",
    "drain_pending_notifications",
    "peek_pending_notifications",
    "clear_pending_notifications",
]
