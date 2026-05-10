"""App-level keybinding chord tracker (now resolver-backed).

Ports the "chord" pattern used by ``typescript/src/keybindings/``:
key sequences like ``g g`` (jump to top), ``[ c`` (prev-change),
``] c`` (next-change) need to remember the first key for a short
window so the second key can complete the chord. In the ink
reference this lives inside ``useInputChord`` — the equivalent here
is :class:`ChordTracker`, a tiny state machine the app feeds with
every key event.

**WI-2.4 (Phase 2 of the ch13 refactor)** rewires the tracker to
dispatch off :class:`KeybindingResolver` rather than the previous
hard-coded prefix-walk. The public surface (``ChordBinding``,
``add_binding``, ``on_key``, ``pending``, ``clear``,
``default_bindings``, ``make_default_tracker``) is unchanged so
existing call-sites and tests keep working. What changes
*semantically*:

* Longest-match-wins: when both a single key and a chord starting
  with that key are bound, the resolver returns ``PENDING`` for the
  buffered single key and only fires the shorter binding once the
  ``timeout_seconds`` window elapses. Vim's ``timeoutlen``.
* Context-aware bindings: the resolver accepts a ``when`` clause +
  active-context set. ``ChordTracker`` itself does NOT manage the
  active-context stack — :class:`KeybindingRegistry` (WI-2.5) owns
  that. Today every binding the tracker carries is global; when the
  registry takes over wiring (WI-2.6), it will populate the tracker
  with bindings carrying ``when`` values.

Binding matches emit a string action id (``"transcript.top"``,
``"message.prev"``, …) which the app dispatches. Unknown chords time
out after ``timeout_seconds`` and are forgotten silently so the user
does not accumulate broken state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from .keybindings_resolver import KeybindingResolver, ResolveStatus
from .keybindings_schema import KeybindingEntry


@dataclass
class ChordBinding:
    """A single chord mapping, e.g. ``["g", "g"] → "transcript.top"``.

    Kept as a thin record (keys + action + description) for back-compat
    with callers that import it directly. Internally :class:`ChordTracker`
    converts these to :class:`KeybindingEntry` instances when building
    the resolver.
    """

    keys: tuple[str, ...]
    action: str
    description: str = ""


@dataclass
class ChordTracker:
    """Accumulates key presses and emits actions on complete chords.

    ``bindings`` is read-only from the outside; use
    :meth:`add_binding` to grow the set. ``timeout_seconds`` defines
    how long the tracker waits between keys before forgetting the
    prefix (typical vim default is 1s).
    """

    bindings: list[ChordBinding] = field(default_factory=list)
    timeout_seconds: float = 1.0
    _buffer: list[str] = field(default_factory=list)
    _last_key_at: float = 0.0
    _context: frozenset[str] = field(default_factory=frozenset)

    # ---- registration ----
    def add_binding(self, keys: tuple[str, ...], action: str, description: str = "") -> None:
        if not keys:
            raise ValueError("chord keys cannot be empty")
        self.bindings.append(
            ChordBinding(keys=tuple(keys), action=action, description=description)
        )

    def clear(self) -> None:
        self.bindings.clear()
        self._buffer.clear()

    def set_context(self, context: Iterable[str]) -> None:
        """Update the active-context set used by the resolver.

        :class:`KeybindingRegistry` (WI-2.5) calls this when contexts are
        added or removed. Today the tracker has no context-scoped
        bindings, so the value is ignored by ``_resolve``; the API is
        present so WI-2.6's wiring is mechanical.
        """

        self._context = frozenset(context)

    # ---- processing ----
    def on_key(self, key: str, *, now: float | None = None) -> str | None:
        """Feed ``key`` into the tracker. Returns the action id on match.

        Resolution uses :class:`KeybindingResolver` under the hood:

        * ``MATCHED`` — clear the buffer, return the action.
        * ``PENDING`` — keep the buffer, return ``None`` (caller types
          the next key OR the timeout fires on the next ``on_key`` call).
        * ``NO_MATCH`` — clear the buffer, return ``None``.

        Timeout handling stays at this layer: if more than
        ``timeout_seconds`` elapsed between keys, the buffer is
        forgotten *before* the new key is appended. Vim's ``timeoutlen``.
        """

        ts = now if now is not None else time.monotonic()
        if self._buffer and (ts - self._last_key_at) > self.timeout_seconds:
            # Before discarding a pending buffer, give the resolver one
            # last chance to resolve it as MATCHED — this is what makes
            # vim-style "single 'g' fires after timeout, even though 'g g'
            # is bound" work. If the buffer doesn't resolve standalone we
            # drop it silently and start fresh.
            timed_out = self._resolve_current_buffer()
            self._buffer.clear()
            self._last_key_at = ts
            if timed_out is not None:
                # Re-queue the new key so the next ``on_key`` starts clean.
                # We can't return both ``timed_out`` and process ``key``;
                # the caller is expected to feed one key per call. Buffer
                # the new key by appending after dispatch.
                self._buffer.append(key)
                self._last_key_at = ts
                return timed_out

        self._buffer.append(key)
        self._last_key_at = ts

        resolver = self._build_resolver()
        result = resolver.resolve(self._buffer, self._context)

        if result.status is ResolveStatus.MATCHED:
            self._buffer.clear()
            return result.action
        if result.status is ResolveStatus.PENDING:
            return None
        # NO_MATCH — buffer cannot extend; reset so the user starts fresh.
        self._buffer.clear()
        return None

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(self._buffer)

    # ---- internals ----
    def _build_resolver(self) -> KeybindingResolver:
        """Construct a resolver from the current ``bindings`` list.

        Cheap (tuple copy + list pass), so we rebuild on every ``on_key``
        call rather than caching with invalidation. Avoids stale-cache
        bugs when callers append bindings after initial construction.
        """

        return KeybindingResolver(
            KeybindingEntry(
                action=b.action, keys=b.keys, description=b.description
            )
            for b in self.bindings
        )

    def _resolve_current_buffer(self) -> str | None:
        """Try to resolve the buffer standalone; return action if MATCHED.

        Called only from the timeout path. Passes ``committed=True`` to the
        resolver so the longest-match-wins extension check is suppressed —
        otherwise a buffer like ``["g"]`` (with both ``("g",)`` and
        ``("g","g")`` bound) would return PENDING forever and the shorter
        binding would never fire post-timeout. Vim's ``timeoutlen`` behavior.
        """

        if not self._buffer:
            return None
        result = self._build_resolver().resolve(
            self._buffer, self._context, committed=True
        )
        if result.status is ResolveStatus.MATCHED:
            return result.action
        return None


def default_bindings() -> list[ChordBinding]:
    """Return the default chord set shared by ink and Textual UIs.

    Kept here as a standalone function so tests can snapshot the
    available chords independently of the running app. Sourced from
    :data:`src.tui.keybindings_loader.DEFAULT_BINDINGS` so the legacy
    chord-tracker view stays in sync with the configurable layer.
    """

    from .keybindings_loader import DEFAULT_BINDINGS

    return [
        ChordBinding(
            keys=tuple(entry.keys),
            action=entry.action,
            description=entry.description,
        )
        for entry in DEFAULT_BINDINGS
    ]


def make_default_tracker() -> ChordTracker:
    tracker = ChordTracker()
    for binding in default_bindings():
        tracker.add_binding(binding.keys, binding.action, binding.description)
    return tracker


def make_tracker_from_entries(entries: Iterable[KeybindingEntry]) -> ChordTracker:
    """Build a tracker from typed :class:`KeybindingEntry` records.

    This is the WI-2.6 entry point: callers that have already loaded +
    merged a user config (via ``load_user_bindings``) build the tracker
    from the merged result, not from the legacy ``default_bindings()``.
    """

    tracker = ChordTracker()
    for entry in entries:
        tracker.add_binding(
            keys=tuple(entry.keys),
            action=entry.action,
            description=entry.description,
        )
    return tracker


__all__ = [
    "ChordBinding",
    "ChordTracker",
    "default_bindings",
    "make_default_tracker",
    "make_tracker_from_entries",
]
