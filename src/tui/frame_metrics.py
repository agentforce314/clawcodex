"""Frame-timing observability surface (gated on ``CLAWCODEX_DEBUG_REPAINTS``).

Phase-11 of the ch13 refactor (gap #10) ports the chapter's
:class:`FrameEvent` shape (``frame.ts``) into a Python observability
surface. The chapter's TS implementation emits a per-frame record with
``phases``, ``yogaVisited``, ``yogaMeasured``, etc. Python's TUI
delegates rendering to Textual, which has its own internal timings —
the goal here is NOT to instrument Textual (out of scope) but to give
Python code a parity record + observer API so future hooks (e.g.
"flush a frame after streaming N tokens") can subscribe.

The module is **disabled by default**. Setting the env var
``CLAWCODEX_DEBUG_REPAINTS=1`` flips ``is_enabled()`` to ``True`` and
unblocks emission. With the env var off, :func:`emit_frame_event` is a
no-op (single ``if`` branch — zero allocation), so production callers
can emit on every commit cycle without paying observability cost when
the user hasn't opted in.

Public surface:

* :class:`FrameEvent` — typed timing record matching the chapter shape.
* :func:`emit_frame_event` — gated emit; observers are notified.
* :func:`register_frame_observer` — subscribe; returns an unregister callable.
* :func:`is_enabled` — env-var probe.
* :data:`FRAME_DEBUG_ENV` — the env var name (``CLAWCODEX_DEBUG_REPAINTS``).

Observer call-out: handlers run synchronously on the emitting thread.
A handler that raises propagates up through ``emit_frame_event`` —
caller code should swallow / log accordingly. We don't suppress
exceptions in the emit path so test code can detect handler bugs.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Final


FRAME_DEBUG_ENV: Final[str] = "CLAWCODEX_DEBUG_REPAINTS"


@dataclass(frozen=True)
class FrameEvent:
    """One frame's worth of timing data.

    Mirrors the chapter ``FrameEvent`` shape:
    * ``duration_ms`` — total commit→write wall time.
    * ``phases`` — sub-stage breakdown. Keys parallel the TS
      ``{ renderer, diff, optimize, write, yoga }`` set; missing keys
      mean "not measured at this layer".
    * ``component_attribution`` — which React/Textual component owns
      the most expensive cell in the frame (``CLAUDE_CODE_DEBUG_REPAINTS``
      analogue). Optional.
    * ``yoga_visited`` / ``yoga_measured`` — layout-pass counts.
    * ``flickers`` — bookkeeping for full-screen reset attribution.
    """

    duration_ms: float
    phases: dict[str, float] = field(default_factory=dict)
    component_attribution: str | None = None
    yoga_visited: int = 0
    yoga_measured: int = 0
    flickers: tuple[str, ...] = ()


_observers: list[Callable[[FrameEvent], None]] = []
_lock = Lock()


def is_enabled() -> bool:
    """True iff ``CLAWCODEX_DEBUG_REPAINTS=1`` is set in the environment.

    Probed on every call (no caching) so test code can flip the env var
    mid-test without a re-import dance.
    """

    return os.environ.get(FRAME_DEBUG_ENV) == "1"


def register_frame_observer(
    callback: Callable[[FrameEvent], None],
) -> Callable[[], None]:
    """Subscribe to frame events. Returns an unregister callable.

    Idempotent: calling the unregister callable twice is a no-op (the
    second call doesn't raise even if the first removed the observer).

    Observers fire synchronously on whatever thread emits the event.
    Long-running observers should hand off to a worker.
    """

    with _lock:
        _observers.append(callback)

    unregistered = [False]

    def _unregister() -> None:
        if unregistered[0]:
            return
        with _lock:
            try:
                _observers.remove(callback)
            except ValueError:
                pass
        unregistered[0] = True

    return _unregister


def emit_frame_event(event: FrameEvent) -> None:
    """Notify subscribed observers — no-op when env var is unset.

    The early-return path is the common case and must remain cheap;
    we deliberately don't iterate observers or take the lock when the
    env var is off.
    """

    if not is_enabled():
        return

    with _lock:
        snapshot = list(_observers)
    for observer in snapshot:
        observer(event)


def clear_observers_for_tests() -> None:
    """Reset the observer list. Test helper only — never call from prod."""

    with _lock:
        _observers.clear()


class TimedPhase:
    """Context manager that records a phase duration into a dict.

    Use::

        phases: dict[str, float] = {}
        with TimedPhase(phases, "render"):
            do_render()
        emit_frame_event(FrameEvent(duration_ms=..., phases=phases))

    Cheap when ``CLAWCODEX_DEBUG_REPAINTS`` is unset (still measures, but
    the frame event is not emitted). Callers that want zero overhead
    when disabled should wrap the whole block in ``if is_enabled():``.
    """

    __slots__ = ("_target", "_key", "_started")

    def __init__(self, target: dict[str, float], key: str) -> None:
        self._target = target
        self._key = key
        self._started = 0.0

    def __enter__(self) -> "TimedPhase":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = (time.perf_counter() - self._started) * 1000.0
        # Accumulate so a phase used twice in one frame sums correctly.
        self._target[self._key] = self._target.get(self._key, 0.0) + elapsed


__all__ = [
    "FRAME_DEBUG_ENV",
    "FrameEvent",
    "TimedPhase",
    "clear_observers_for_tests",
    "emit_frame_event",
    "is_enabled",
    "register_frame_observer",
]
