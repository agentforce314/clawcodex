"""Ch5/G.3 — slash-command + task-notification lifecycle dispatch.

Mirrors TS ``notifyCommandLifecycle`` at query.ts:1746-1823. The
two-layer ``query()`` entry point (G.3) tracks UUIDs of slash
commands and task notifications consumed during a turn; after the
inner loop terminates NATURALLY (not via .aclose() or exception),
the outer wrapper fires ``notify_command_lifecycle(uuid,
"completed")`` for every consumed UUID. A failed turn does NOT
declare its commands successful — chapter §"The Two-Layer Entry
Point" documents the rationale.

Today this is a minimal pub-sub stub: command-queue + slash-command
infrastructure isn't wired into the Python port yet (the
``_drain_pending_user_messages`` helper at query.py:119 drains the
*agent-task* inbox, not a global command queue). When that
infrastructure lands, the listener registry below is the integration
seam.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal

logger = logging.getLogger(__name__)

LifecycleStatus = Literal["started", "completed", "failed"]

# Listeners are simple callables that receive (uuid, status). Tests
# can register a listener to assert that lifecycle events fired.
_listeners: list[Callable[[str, LifecycleStatus], None]] = []


def notify_command_lifecycle(uuid: str, status: LifecycleStatus) -> None:
    """Notify all registered listeners that a command UUID transitioned
    to ``status``.

    Statuses:
      * ``"started"`` — fired when the loop begins consuming the
        command. Currently NOT emitted by the loop because Python
        doesn't have the slash-command queue yet; reserved for
        future integration.
      * ``"completed"`` — fired by the outer ``query()`` wrapper
        AFTER the inner loop terminates naturally. Skipped on
        ``.aclose()``/exception so failed turns don't falsely
        declare success.
      * ``"failed"`` — reserved for future granular failure
        reporting.
    """
    for listener in list(_listeners):
        try:
            listener(uuid, status)
        except Exception:
            logger.exception(
                "Command-lifecycle listener raised for uuid=%s status=%s",
                uuid, status,
            )


def register_lifecycle_listener(
    listener: Callable[[str, LifecycleStatus], None],
) -> Callable[[], None]:
    """Register a listener and return an unregister callable."""
    _listeners.append(listener)

    def _unregister() -> None:
        try:
            _listeners.remove(listener)
        except ValueError:
            pass

    return _unregister


def clear_lifecycle_listeners() -> None:
    """Test helper — drop all registered listeners."""
    _listeners.clear()
