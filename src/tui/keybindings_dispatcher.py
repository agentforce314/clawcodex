"""Glue layer that ties ChordTracker + KeybindingRegistry together.

Phase 2 of the ch13 refactor splits keybinding handling into three pieces:

* :class:`KeybindingResolver` — pure "key sequence → action id" mapping.
* :class:`ChordTracker` — buffer + timeout state machine, dispatches off
  the resolver. Returns action ids on chord completion.
* :class:`KeybindingRegistry` — per-widget handlers; given an action id,
  fires the most-specific active handler.

This module wires the three together via :class:`KeybindingDispatcher` —
one object an app or screen instantiates and feeds raw key events; the
dispatcher routes via the tracker and fires via the registry. Production
callers don't need to know any of the layer-specific types.

Mirrors the role of the chapter's ``KeybindingProviderSetup.tsx``: a
single integration point widgets and screens can live behind.
"""

from __future__ import annotations

from typing import Iterable

from .keybindings import ChordTracker, make_tracker_from_entries
from .keybindings_loader import DEFAULT_BINDINGS, load_user_bindings
from .keybindings_registry import KeybindingRegistry, RegisteredBinding
from .keybindings_schema import KeybindingEntry


class KeybindingDispatcher:
    """One-stop API for hooking the keybinding layer into a Textual app.

    Typical lifecycle (used by a Textual screen)::

        # On screen mount:
        dispatcher = KeybindingDispatcher.from_user_config()

        # Register a handler from a widget:
        unreg = dispatcher.register(
            action="transcript.clear",
            handler=transcript.clear_transcript,
        )

        # On key event from Textual:
        dispatcher.feed_key(event.key)

        # On widget unmount:
        unreg()

    Two round-trip guarantees the test suite exercises in
    ``tests/tui/test_keybindings_pipeline.py``:

    1. User configs at ``~/.claude/keybindings.json`` override defaults
       through the same dispatcher (no special "user" path).
    2. Actions for which no handler is registered silently fail — the
       dispatcher returns ``False`` rather than raising. Mirrors the
       chapter's "registered handler is the binding's value, not its
       gate" semantic.
    """

    def __init__(
        self,
        bindings: Iterable[KeybindingEntry],
        *,
        registry: KeybindingRegistry | None = None,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._tracker: ChordTracker = make_tracker_from_entries(bindings)
        # Set timeout post-construction since ``make_tracker_from_entries``
        # uses the dataclass default. Cheap; one assignment.
        self._tracker.timeout_seconds = timeout_seconds
        self._registry: KeybindingRegistry = registry or KeybindingRegistry()

    # ---- alternate constructors ----
    @classmethod
    def from_defaults(
        cls,
        *,
        registry: KeybindingRegistry | None = None,
        timeout_seconds: float = 1.0,
    ) -> "KeybindingDispatcher":
        """Build a dispatcher carrying only the built-in default bindings.

        Useful for tests and for environments where a user config file
        should never be consulted (e.g. deterministic snapshot tests).
        """

        return cls(
            DEFAULT_BINDINGS,
            registry=registry,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def from_user_config(
        cls,
        *,
        registry: KeybindingRegistry | None = None,
        timeout_seconds: float = 1.0,
    ) -> "KeybindingDispatcher":
        """Build a dispatcher carrying defaults + user overrides.

        Reads ``~/.claude/keybindings.json`` via :func:`load_user_bindings`;
        falls back to defaults silently when the file is absent / invalid
        (the loader logs the failure mode).
        """

        return cls(
            load_user_bindings(),
            registry=registry,
            timeout_seconds=timeout_seconds,
        )

    # ---- handler registration ----
    def register(
        self,
        action: str,
        handler,
        *,
        context_name: str | None = None,
        is_active=None,
    ):
        """Register a handler for ``action``. Returns an unregister callable."""

        return self._registry.register(
            RegisteredBinding(
                action=action,
                handler=handler,
                context_name=context_name,
                is_active=is_active,
            )
        )

    # ---- context lifecycle ----
    def add_context(self, name: str) -> None:
        self._registry.add_context(name)
        self._tracker.set_context(self._registry.active_contexts())

    def remove_context(self, name: str) -> None:
        self._registry.remove_context(name)
        self._tracker.set_context(self._registry.active_contexts())

    def active_contexts(self) -> set[str]:
        return self._registry.active_contexts()

    # ---- feed events ----
    def feed_key(self, key: str, *, now: float | None = None) -> str | None:
        """Feed one key into the tracker; fire any matching handler.

        Returns the action id that fired, or ``None`` if the key was
        absorbed (chord still pending) or no chord matched. Used by tests
        and by callers that want to log dispatch outcomes.
        """

        action = self._tracker.on_key(key, now=now)
        if action is None:
            return None
        self._registry.fire(action)
        return action

    def fire(self, action: str) -> bool:
        """Bypass the chord tracker and fire an action by name.

        For callers (e.g. Textual ``BINDINGS``-driven screens) where the
        host framework has already resolved the keystroke to an action.
        Returns ``True`` iff a registered handler ran.
        """

        return self._registry.fire(action)

    # ---- introspection ----
    @property
    def tracker(self) -> ChordTracker:
        return self._tracker

    @property
    def registry(self) -> KeybindingRegistry:
        return self._registry


__all__ = [
    "KeybindingDispatcher",
]
