"""Per-component keybinding registration with refcounted active contexts.

Mirrors the chapter's
``typescript/src/keybindings/{useKeybinding.ts, KeybindingContext.tsx}``
mechanism — a registry that lets widgets attach handlers for action ids,
optionally scoped to a named context, with the registry tracking which
contexts are currently active.

Refactoring-plan WI-2.5 (and the Critic-flagged clarification): contexts
form an **unordered set with reference counting**, NOT a stack. The same
context name can be activated by multiple sources simultaneously (nested
re-mount of a focused widget, two non-overlapping widgets sharing a
context name) and remains active until ALL sources have called
:meth:`remove_context`. ``add_context`` increments the count;
``remove_context`` decrements; the context is "active" iff its count is
> 0.

Public surface:

* :class:`RegisteredBinding` — typed registration record.
* :class:`KeybindingRegistry` — the registry.

Note this module does NOT consult the keybindings config (loader /
resolver) directly — it only handles the **handler dispatch** side.
The key→action mapping happens upstream in :class:`ChordTracker` /
:class:`KeybindingResolver` (Phase 2 WI-2.3 / WI-2.4); the registry
fires the action on the most-specific active handler.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Final


@dataclass(frozen=True)
class RegisteredBinding:
    """Pointer + handler for one registration call.

    * ``action`` — the action id this handler responds to (matches
      :class:`KeybindingEntry.action` from the configuration layer).
    * ``handler`` — zero-arg callable invoked when the registry fires.
    * ``context_name`` — when present, the binding only fires when this
      context is in the active set. ``None`` = global (always candidate).
    * ``is_active`` — optional dynamic predicate. When supplied, the
      binding fires only if the predicate returns ``True`` AT THE FIRE
      MOMENT. ``None`` means "always live as long as ``context_name`` is
      active".

    **Divergence from the chapter (documented; follow-up ticket):** the
    chapter's ``useKeybinding.ts:43`` short-circuits at register time so
    a ``False`` predicate effectively de-registers the handler — and a
    less-specific binding (e.g. global) cascades to fire instead. This
    Python implementation blocks at fire time WITHOUT cascading: when
    the most-specific scoped handler's ``is_active`` returns ``False``,
    :meth:`KeybindingRegistry.fire` returns ``False`` immediately rather
    than searching for a less-specific candidate. The Phase-2 plan
    accepts the simpler semantic; a future ticket can implement cascade
    when a widget actually needs it (none do today). Until then,
    consumers that want cascade behavior should call ``unreg()`` from
    the predicate's transition rather than relying on ``is_active``.
    """

    action: str
    handler: Callable[[], None]
    context_name: str | None = None
    is_active: Callable[[], bool] | None = None


_Unregister = Callable[[], None]
"""Callable returned by :meth:`KeybindingRegistry.register`. Calling it
removes the registration; idempotent (calling twice is a no-op)."""


class KeybindingRegistry:
    """Track per-component handlers + the refcounted active-context set.

    Thread-safe (handlers may be invoked from Textual's event loop while a
    background thread re-mounts a widget). All public methods take the
    internal lock; handlers themselves run *outside* the lock.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        # Insertion-ordered list: deeper-in-tree widgets mount last, so
        # registrations near the end of the list win the most-specific
        # tiebreak. The :class:`Final` annotation captures the structural
        # invariant — we never re-bind ``self._bindings`` to a new list,
        # only mutate the existing one.
        self._bindings: Final[list[RegisteredBinding]] = []
        # Reference count per context name. Insertion of a key implies
        # count >= 1; ``remove_context`` decrements and pops at zero.
        self._context_counts: Counter[str] = Counter()

    # ---- registration ----
    def register(self, binding: RegisteredBinding) -> _Unregister:
        """Register ``binding`` and return a callable that removes it.

        Calling the returned callable twice is a no-op — it tracks
        whether the binding is still in the list before attempting the
        removal so widget unmount paths can call it unconditionally.
        """

        with self._lock:
            self._bindings.append(binding)

        unregistered = [False]

        def _unregister() -> None:
            if unregistered[0]:
                return
            with self._lock:
                # ``list.remove`` is O(n); fine for the n ≈ widget count
                # scales we work at. Use ``identity-aware`` removal so two
                # equal RegisteredBinding values registered separately
                # remove the right one.
                for i, b in enumerate(self._bindings):
                    if b is binding:
                        del self._bindings[i]
                        break
            unregistered[0] = True

        return _unregister

    # ---- context lifecycle ----
    def add_context(self, name: str) -> None:
        """Increment the refcount for ``name``; activate at first add.

        Refcount semantics — calling ``add_context("foo")`` twice and
        then ``remove_context("foo")`` once leaves ``foo`` active. The
        context only deactivates when the refcount returns to zero.
        """

        if not name:
            raise ValueError("context name cannot be empty")
        with self._lock:
            self._context_counts[name] += 1

    def remove_context(self, name: str) -> None:
        """Decrement the refcount for ``name``; deactivate at zero.

        Decrementing an inactive context is a silent no-op (rather than
        raising) because widget unmount can race with parent unmount and
        emit duplicate ``remove_context`` calls; raising would surface
        as a confusing crash inside Textual's lifecycle.
        """

        with self._lock:
            if self._context_counts[name] <= 0:
                return
            self._context_counts[name] -= 1
            if self._context_counts[name] == 0:
                del self._context_counts[name]

    def active_contexts(self) -> set[str]:
        """Return a snapshot of currently-active context names."""

        with self._lock:
            return set(self._context_counts)

    # ---- dispatch ----
    def fire(self, action: str) -> bool:
        """Find + invoke the most-specific active handler for ``action``.

        Most-specific = (a) ``context_name`` matches an active context;
        OR (b) ``context_name`` is ``None`` (global). Context-scoped
        handlers beat global ones when both match. Among multiple
        context-scoped candidates, the LAST registered wins — deeper
        widgets mount last and therefore appear later in the binding
        list. Among multiple global candidates, the LAST registered wins
        for the same reason.

        Returns ``True`` iff a handler was found and invoked.

        ``is_active`` is consulted at fire time (not at register time)
        so dynamic widgets (e.g. modals that briefly disable their own
        keys during animations) can opt out without unregistering.
        """

        with self._lock:
            active = set(self._context_counts)
            # Walk the binding list in reverse order so the latest-registered
            # candidate is found first; that matches "deeper widget wins".
            scoped_candidate: RegisteredBinding | None = None
            global_candidate: RegisteredBinding | None = None
            for binding in reversed(self._bindings):
                if binding.action != action:
                    continue
                if binding.context_name is not None:
                    if binding.context_name not in active:
                        continue
                    if scoped_candidate is None:
                        scoped_candidate = binding
                else:
                    if global_candidate is None:
                        global_candidate = binding
                # Once we have a scoped winner we can stop — context-scoped
                # always beats global, so further iteration cannot improve
                # on the answer.
                if scoped_candidate is not None:
                    break
            chosen = scoped_candidate or global_candidate

        # Invoke outside the lock so a handler that calls back into the
        # registry (e.g. unregisters itself or registers a new binding)
        # cannot deadlock.
        if chosen is None:
            return False
        if chosen.is_active is not None and not chosen.is_active():
            return False
        chosen.handler()
        return True

    # ---- introspection (test seam) ----
    def __len__(self) -> int:
        with self._lock:
            return len(self._bindings)

    def bindings_for(self, action: str) -> list[RegisteredBinding]:
        """Snapshot of all registered bindings whose ``action`` matches."""

        with self._lock:
            return [b for b in self._bindings if b.action == action]


__all__ = [
    "KeybindingRegistry",
    "RegisteredBinding",
]
