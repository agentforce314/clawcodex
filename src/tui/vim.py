"""Minimal vim-mode state machine for the Textual prompt.

Ports the subset of ``typescript/src/keybindings/`` that the ink
reference actually uses when the user enables ``vimMode`` in config:
Normal / Insert modes, single-character chord parsing, and a handful
of motions that work inside a single-line :class:`Input` widget.

The state machine is intentionally decoupled from Textual so it can
be unit-tested without a running app. The :class:`PromptInput`
consumes it via :meth:`PromptInput.apply_vim_action`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Mode(str, Enum):
    INSERT = "insert"
    NORMAL = "normal"


# Every action the state machine can emit. The :class:`PromptInput`
# applies them to its underlying ``Input`` widget.
VimAction = Literal[
    "insert-before",
    "insert-after",
    "insert-line-start",
    "insert-line-end",
    "move-left",
    "move-right",
    "move-start",
    "move-end",
    "move-word-next",
    "move-word-prev",
    "delete-char",
    "delete-line",
    "yank-line",
    "paste-after",
    "paste-before",
    "enter-normal",
    "enter-insert",
    "submit",
]


@dataclass
class VimResult:
    """What the caller should do after processing a key press."""

    consumed: bool
    action: VimAction | None = None
    mode: Mode = Mode.NORMAL


class VimState:
    """Tiny stateful parser for a single :class:`Input` widget.

    Responds to:

    * ``Escape`` → enter Normal mode.
    * ``i`` / ``I`` → insert-before / insert-line-start.
    * ``a`` / ``A`` → insert-after / insert-line-end.
    * ``h`` ``j`` ``k`` ``l`` → move-left / prev-history /
      next-history / move-right (j/k are passed through as history
      nav because we're single-line).
    * ``0`` / ``$`` → jump to start / end.
    * ``w`` / ``b`` → next / previous word.
    * ``x`` → delete char under cursor.
    * ``dd`` → delete whole line.
    * ``yy`` → yank whole line.
    * ``p`` / ``P`` → paste after / before cursor.
    * ``:w`` or Enter in Normal → submit.

    All unknown keys in Normal mode are consumed silently (so the
    widget never ends up typing "h" into the buffer when the user
    expects a motion).
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self._mode: Mode = Mode.INSERT
        self._pending: str = ""  # for two-char chords (dd, yy)

    # ---- public control ----
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._mode = Mode.INSERT
            self._pending = ""

    @property
    def mode(self) -> Mode:
        return self._mode

    def reset(self) -> None:
        self._pending = ""
        self._mode = Mode.INSERT

    # ---- key handling ----
    def handle(self, key: str) -> VimResult:
        """Process ``key`` and return the resulting action (if any)."""

        if not self._enabled:
            return VimResult(consumed=False, mode=self._mode)

        # Escape always returns to Normal.
        if key == "escape":
            was_insert = self._mode is Mode.INSERT
            self._mode = Mode.NORMAL
            self._pending = ""
            return VimResult(
                consumed=was_insert,
                action="enter-normal",
                mode=self._mode,
            )

        if self._mode is Mode.INSERT:
            # Insert mode: pass every key through to the underlying
            # Input so it behaves like a plain text field.
            return VimResult(consumed=False, mode=self._mode)

        # Normal mode --------------------------------------------------
        # Handle two-character chords first.
        if self._pending:
            pending, self._pending = self._pending, ""
            if pending == "d" and key == "d":
                return VimResult(consumed=True, action="delete-line", mode=self._mode)
            if pending == "y" and key == "y":
                return VimResult(consumed=True, action="yank-line", mode=self._mode)
            # Unknown chord — drop it silently but still consume the key
            # so it doesn't leak into the buffer.
            return VimResult(consumed=True, mode=self._mode)

        action: VimAction | None = None
        next_mode = self._mode

        if key == "i":
            action, next_mode = "insert-before", Mode.INSERT
        elif key == "I":
            action, next_mode = "insert-line-start", Mode.INSERT
        elif key == "a":
            action, next_mode = "insert-after", Mode.INSERT
        elif key == "A":
            action, next_mode = "insert-line-end", Mode.INSERT
        elif key == "h":
            action = "move-left"
        elif key == "l":
            action = "move-right"
        elif key in ("j",):
            # Single-line: j/k are history nav; caller routes them.
            action = "move-right"  # no-op semantically; keeps binding consumed
        elif key in ("k",):
            action = "move-left"
        elif key == "0":
            action = "move-start"
        elif key == "$":
            action = "move-end"
        elif key == "w":
            action = "move-word-next"
        elif key == "b":
            action = "move-word-prev"
        elif key == "x":
            action = "delete-char"
        elif key == "d":
            self._pending = "d"
            return VimResult(consumed=True, mode=self._mode)
        elif key == "y":
            self._pending = "y"
            return VimResult(consumed=True, mode=self._mode)
        elif key == "p":
            action = "paste-after"
        elif key == "P":
            action = "paste-before"
        elif key == "enter":
            action = "submit"
        else:
            # Unknown / unsupported → still consume so it doesn't type
            # into the buffer.
            return VimResult(consumed=True, mode=self._mode)

        self._mode = next_mode
        return VimResult(consumed=True, action=action, mode=self._mode)


__all__ = ["Mode", "VimAction", "VimResult", "VimState"]
