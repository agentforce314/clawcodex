"""App-level keybinding chord tracker.

Ports the "chord" pattern used by ``typescript/src/keybindings/``:
key sequences like ``g g`` (jump to top), ``[ c`` (prev-change),
``] c`` (next-change) need to remember the first key for a short
window so the second key can complete the chord. In the ink
reference this lives inside ``useInputChord`` — the equivalent here
is :class:`ChordTracker`, a tiny state machine the app feeds with
every key event.

Binding matches emit a string action id (``"transcript.top"``,
``"message.prev"``, …) which the app dispatches. Unknown chords time
out after ``timeout_seconds`` and are forgotten silently so the user
does not accumulate broken state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ChordBinding:
    """A single chord mapping, e.g. ``["g", "g"] → "transcript.top"``."""

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

    # ---- registration ----
    def add_binding(self, keys: tuple[str, ...], action: str, description: str = "") -> None:
        if not keys:
            raise ValueError("chord keys cannot be empty")
        self.bindings.append(
            ChordBinding(keys=tuple(keys), action=action, description=description)
        )

    def clear(self) -> None:
        self.bindings.clear()

    # ---- processing ----
    def on_key(self, key: str, *, now: float | None = None) -> str | None:
        """Feed ``key`` into the tracker. Returns the action id on match."""

        ts = now if now is not None else time.monotonic()
        if self._buffer and (ts - self._last_key_at) > self.timeout_seconds:
            self._buffer.clear()
        self._buffer.append(key)
        self._last_key_at = ts

        # Exact match?
        chord = tuple(self._buffer)
        for binding in self.bindings:
            if binding.keys == chord:
                self._buffer.clear()
                return binding.action

        # Does the current buffer match any binding's prefix? If not,
        # the buffer cannot grow into a match and must be reset. This
        # mirrors vim's behaviour: typing ``z`` when no chord starts
        # with ``z`` immediately fails the chord.
        if not any(binding.keys[: len(chord)] == chord for binding in self.bindings):
            self._buffer.clear()
        return None

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(self._buffer)


def default_bindings() -> list[ChordBinding]:
    """Return the default chord set shared by ink and Textual UIs.

    Kept here as a standalone function so tests can snapshot the
    available chords independently of the running app.
    """

    return [
        ChordBinding(("g", "g"), "transcript.top", "Jump to oldest message"),
        ChordBinding(("G",), "transcript.bottom", "Jump to latest message"),
        ChordBinding(("[", "c"), "transcript.prev-change", "Previous tool result"),
        ChordBinding(("]", "c"), "transcript.next-change", "Next tool result"),
        ChordBinding(("[", "m"), "transcript.prev-message", "Previous message"),
        ChordBinding(("]", "m"), "transcript.next-message", "Next message"),
        ChordBinding(("ctrl+w", "v"), "layout.toggle-overlay", "Toggle overlay"),
    ]


def make_default_tracker() -> ChordTracker:
    tracker = ChordTracker()
    for binding in default_bindings():
        tracker.add_binding(binding.keys, binding.action, binding.description)
    return tracker


__all__ = [
    "ChordBinding",
    "ChordTracker",
    "default_bindings",
    "make_default_tracker",
]
