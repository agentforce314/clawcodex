"""Tiny pub/sub primitive for pure event signals (no stored state).

Mirrors ``typescript/src/utils/signal.ts`` (43 LOC). Distinct from a Store —
there is no ``get_state``, no snapshot. Use this when subscribers only need
to know "something happened" (optionally with event args), not "what is the
current value".

Typical pattern in bootstrap state::

    _session_switched: Signal = create_signal()
    on_session_switch = _session_switched.subscribe

    def switch_session(sid: SessionId, ...) -> None:
        ...
        _session_switched.emit(sid)

Listener call order is **insertion-order** — backed by a ``dict[Callable, None]``
which is an ordered set in CPython 3.7+. Callers should not depend on order
as a contract; this is documented behavior for ease of debugging, not part
of the API.

**Exception handling.** A listener that raises propagates the exception
through ``emit`` to the caller — matches TS ``signal.ts`` which does not
catch. Any caller wanting fan-out-with-suppression must build it on top
(typically a try/except wrapper around the listener registration).

**Subscriber-mutation safety.** ``emit`` snapshots the listener set before
iterating, so a listener that calls ``subscribe`` or its own
``unsubscribe`` during the callback does not corrupt the iteration. A
newly-subscribed listener will **not** receive the in-flight emit; it
fires on the next ``emit``.

**Deliberate divergence from TS.** TS ``signal.ts`` iterates the live
``Set``, so a listener that subscribes a new listener during emit
**would** fire the new listener on the same emit (per JS Set iterator
semantics). The Python port uses snapshot iteration as a defensive
choice — same-emit fan-out from a freshly-subscribed listener is rarely
intentional and is harder to reason about. Tests at
``tests/test_signal.py:84-101`` lock the snapshot behavior so a future
refactor doesn't silently re-match TS at the cost of breaking callers
that depend on the current semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Signal:
    """Listener-set primitive with ``subscribe``/``emit``/``clear``.

    ``emit`` arguments are untyped (``*args: Any``) — the TS reference uses
    ``ParamSpec`` for typed event args, but Python's ``Protocol[ParamSpec]``
    has historically been flaky on mypy. The plan defers typed-emit to a
    follow-up WI; see ``my-docs/ch03-state-refactoring-plan.md`` §Risks.
    """

    # Ordered set: dict-with-None-values preserves insertion order on
    # CPython 3.7+. Hash+equality-based deduplication, same as ``set``.
    _listeners: dict[Callable[..., None], None] = field(default_factory=dict)

    def subscribe(self, listener: Callable[..., None]) -> Callable[[], None]:
        """Register ``listener``. Returns an idempotent unsubscribe function."""
        self._listeners[listener] = None

        def unsubscribe() -> None:
            self._listeners.pop(listener, None)

        return unsubscribe

    def emit(self, *args: Any, **kwargs: Any) -> None:
        """Call every subscribed listener with the given args.

        Iterates a snapshot taken at emit-entry; mid-iteration
        subscribe/unsubscribe does not affect the current emit. Exceptions
        raised by listeners propagate to the caller.
        """
        for listener in list(self._listeners):
            listener(*args, **kwargs)

    def clear(self) -> None:
        """Remove all listeners. Useful in dispose/reset paths."""
        self._listeners.clear()


def create_signal() -> Signal:
    """Factory matching the TS ``createSignal<Args>()`` name. Returns a
    fresh ``Signal`` with no listeners."""
    return Signal()


__all__ = ["Signal", "create_signal"]
