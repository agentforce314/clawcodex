"""Tiny reactive store: get, set (via updater), subscribe + onChange.

Mirrors ``typescript/src/state/store.ts`` (34 LOC). Designed to back a
single AppState instance per process. The defining properties:

* **Updater function pattern.** Callers pass ``(prev) -> next`` rather than
  a raw new value. This eliminates stale-state bugs from captured-stale
  closures in async paths — every mutation sees the freshest state.

* **Identity skip.** If ``updater(prev) is prev``, the mutation is a no-op
  and no listeners or onChange callbacks fire. Python's ``is`` matches TS
  ``Object.is`` semantics for the use case (no ``NaN``, no ``+0``/``-0``
  involved — state is a dataclass instance).

  **Caller contract:** if the updater wants the no-op skip, it must return
  the *same reference* it received. Returning a freshly-constructed but
  structurally-identical dataclass fires onChange and all listeners. This
  is intentional and matches TS — structural equality would be O(n) per
  mutation and would silently swallow "I built a new object on purpose"
  intent.

* **onChange fires synchronously before listeners.** The bridge to bootstrap
  state and other side effects hook here so they run *before* any
  subscriber re-renders. Listeners see the post-onChange state via
  ``get_state``.

The TS module is `state/store.ts`; here it lives under ``src/utils/`` to
parallel ``src/utils/signal.py`` and avoid `src/state/__init__.py`'s
import-time file I/O. Architectural cost of deviation: zero — the store is
single-purpose and location-agnostic.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")

Listener = Callable[[], None]
OnChange = Callable[[T, T], None]  # (old_state, new_state) -> None


class Store(Generic[T]):
    """Reactive store backed by a single state reference.

    Use ``create_store(initial, on_change=...)`` to construct. The store
    is *not* thread-safe; if multiple threads need to mutate, wrap the
    setter in a lock at the call site.
    """

    def __init__(self, initial_state: T, on_change: OnChange | None = None) -> None:
        self._state: T = initial_state
        # Ordered (insertion-stable) set of listeners; matches Signal.
        self._listeners: dict[Listener, None] = {}
        self._on_change: OnChange | None = on_change

    def get_state(self) -> T:
        """Return the current state reference. Callers must treat the
        return value as immutable — mutating it in place breaks the store's
        identity-skip contract."""
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        """Apply ``updater(prev)`` to produce the next state.

        If ``updater(prev) is prev`` (identity check, not structural
        equality), the call is a no-op. Otherwise:

        1. The new state is committed to ``self._state``.
        2. ``on_change(old, new)`` fires synchronously (if set).
        3. All subscribed listeners fire in insertion order.

        Listener and onChange exceptions propagate to the caller; the
        state has already been committed at that point.
        """
        prev = self._state
        nxt = updater(prev)
        if nxt is prev:
            return
        # Commit state BEFORE side effects so an onChange or listener that
        # reads back via get_state() sees the new value (matches TS).
        self._state = nxt
        if self._on_change is not None:
            self._on_change(prev, nxt)
        # Snapshot before iterating: listeners may unsubscribe themselves
        # or trigger another set_state during the callback.
        for listener in list(self._listeners):
            listener()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register ``listener``. Returns an idempotent unsubscribe."""
        self._listeners[listener] = None

        def unsubscribe() -> None:
            self._listeners.pop(listener, None)

        return unsubscribe


def create_store(initial_state: T, on_change: OnChange | None = None) -> Store[T]:
    """Construct a ``Store`` with the given initial state and optional
    ``on_change`` callback. Factory function matches the TS ``createStore``
    name."""
    return Store(initial_state, on_change)


__all__ = ["Store", "Listener", "OnChange", "create_store"]
