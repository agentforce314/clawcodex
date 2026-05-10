"""Multi-line variant of :class:`PromptInput` backed by Textual ``TextArea``.

Phase-4 WI-4.1 of the ch13 refactor (wave-2). The single-line
:class:`src.tui.widgets.prompt_input.PromptInput` (backed by Textual
``Input``) is preserved unchanged so the existing test surface keeps
working; this multi-line variant is the opt-in widget for hosts that
want the full chapter-grade prompt editor.

Key behaviors preserved from the single-line variant:

* Plain ``Enter`` submits.
* ``Shift+Enter`` / ``Alt+Enter`` / ``Ctrl+J`` insert a newline.
* ``Ctrl+L`` clears the draft.
* Slash-command popup opens on ``/`` and filters live.
* Vim-mode toggle and the multi-line vim modules
  (:mod:`src.tui.vim_buffer`, :mod:`src.tui.vim_text_objects`,
  :mod:`src.tui.vim_operators`) compose with this widget — the host
  app drives them, this widget exposes the buffer state.

Out of scope for this iteration:

* Direct integration of the Phase-4 wave-1 `VimBuffer` model with the
  TextArea text. The widget exposes :meth:`get_buffer` so callers
  building a vim host can snapshot the current text into a
  :class:`VimBuffer` on demand and apply the result via
  :meth:`set_text`.
* Per-keystroke IME caret publication into TextArea (the underlying
  Textual TextArea does its own caret management; the chapter's
  declared-cursor mechanism stays scoped to the single-line variant
  for now).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option

from ..messages import CancelRequested
from ..vim_buffer import VimBuffer
from .prompt_input_footer import PromptInputFooter
from .prompt_input_mode_indicator import PromptInputModeIndicator
from .prompt_input_queued_commands import PromptInputQueuedCommands
from .prompt_input_stash_notice import (
    PromptInputStashNotice,
    clear_stash,
    read_stash,
    write_stash,
)


@dataclass
class PromptSubmitted(Message):
    """User pressed plain ``Enter`` on a non-empty prompt."""

    text: str


class _SlashSuggestions(OptionList):
    DEFAULT_CSS = """
    _SlashSuggestions {
        max-height: 10;
        border: round $primary;
        background: $surface;
    }
    _SlashSuggestions.-hidden {
        display: none;
    }
    """


class PromptInputMulti(Vertical):
    """Multi-line prompt input — TextArea-backed."""

    DEFAULT_CSS = """
    PromptInputMulti {
        height: auto;
        padding: 0;
    }
    PromptInputMulti > TextArea {
        border: round $primary-darken-2;
        height: auto;
        max-height: 10;
        min-height: 1;
    }
    """

    BINDINGS = [
        ("ctrl+l", "clear_draft", "Clear draft"),
    ]

    def __init__(
        self,
        *,
        words_provider: Callable[[], list[str]],
        workspace_root: Path | None = None,
    ) -> None:
        super().__init__()
        self._words_provider = words_provider
        self._history: list[str] = []
        self._history_pos: int | None = None
        self._textarea = TextArea(
            text="",
            soft_wrap=True,
            show_line_numbers=False,
        )
        self._suggestions = _SlashSuggestions(classes="-hidden")
        self._workspace_root = workspace_root or Path.cwd()
        self._mode_indicator = PromptInputModeIndicator()
        self._footer = PromptInputFooter()
        self._queued_commands = PromptInputQueuedCommands()
        self._stash_notice = PromptInputStashNotice()

    def compose(self) -> ComposeResult:
        yield self._stash_notice
        yield self._queued_commands
        yield self._mode_indicator
        yield self._textarea
        yield self._suggestions
        yield self._footer

    def on_mount(self) -> None:
        self._textarea.focus()
        try:
            stashed = read_stash(self._workspace_root)
        except Exception:
            stashed = ""
        self._stash_notice.announce_stash(bool(stashed and stashed.strip()))
        self._refresh_footer()

    # ---- external API ----
    def focus_input(self) -> None:
        self._textarea.focus()

    @property
    def value(self) -> str:
        """Read the current draft text."""

        return self._textarea.text or ""

    def set_value(self, value: str) -> None:
        """Replace the draft text and park the cursor at the end."""

        self._textarea.text = value or ""
        # Move cursor to end of text — TextArea uses (row, col).
        try:
            lines = (value or "").splitlines() or [""]
            self._textarea.cursor_location = (
                len(lines) - 1,
                len(lines[-1]),
            )
        except Exception:
            pass
        self._hide_suggestions()

    def clear(self) -> None:
        self._textarea.text = ""
        self._hide_suggestions()

    def action_clear_draft(self) -> None:
        self.clear()

    def set_enabled(self, enabled: bool) -> None:
        self._textarea.disabled = not enabled
        if enabled:
            self._textarea.focus()

    # ---- VimBuffer bridge ----
    def get_buffer(self) -> VimBuffer:
        """Snapshot the current text into a :class:`VimBuffer`.

        The returned buffer's cursor is set to the TextArea's current
        cursor location. Callers that apply vim operators to the buffer
        write the result back via :meth:`set_text_and_cursor`.
        """

        buf = VimBuffer(self.value)
        try:
            row, col = self._textarea.cursor_location
            buf.set_cursor(row, col)
        except Exception:
            pass
        return buf

    def set_text_and_cursor(self, text: str, cursor: tuple[int, int]) -> None:
        """Apply a buffer-derived ``(text, cursor)`` back to the TextArea."""

        self._textarea.text = text
        try:
            self._textarea.cursor_location = cursor
        except Exception:
            pass

    # ---- queued commands / stash external API ----
    def set_queued_commands(self, queue) -> None:
        self._queued_commands.set_queue(tuple(queue))

    def set_footer_hints(self, hints) -> None:
        self._footer.set_hints(hints)

    def stash_current_draft(self) -> None:
        text = self.value
        if text.strip():
            write_stash(text, self._workspace_root)

    def recover_stashed_draft(self) -> bool:
        text = read_stash(self._workspace_root)
        if not text or not text.strip():
            self._stash_notice.announce_stash(False)
            return False
        self.set_value(text)
        clear_stash(self._workspace_root)
        self._stash_notice.announce_stash(False)
        return True

    @property
    def stash_notice(self) -> PromptInputStashNotice:
        return self._stash_notice

    @property
    def queued_commands(self) -> PromptInputQueuedCommands:
        return self._queued_commands

    @property
    def footer(self) -> PromptInputFooter:
        return self._footer

    @property
    def mode_indicator(self) -> PromptInputModeIndicator:
        return self._mode_indicator

    @property
    def textarea(self) -> TextArea:
        """Test seam — exposes the underlying TextArea."""

        return self._textarea

    # ---- key handling ----
    def on_key(self, event: events.Key) -> None:
        key = event.key

        # Slash-popup escape closes without losing the draft.
        if key == "escape" and not self._suggestions.has_class("-hidden"):
            self._hide_suggestions()
            event.stop()
            return
        if key == "escape":
            self.post_message(CancelRequested())
            event.stop()
            return

        # Plain ``Enter`` submits; ``shift+enter`` / ``alt+enter`` /
        # ``ctrl+j`` insert a newline. ``Ctrl+L`` falls through to the
        # BINDING.
        if key == "enter":
            text = self.value.strip()
            if not self._suggestions.has_class("-hidden"):
                idx = self._suggestions.highlighted
                if idx is not None:
                    option = self._suggestions.get_option_at_index(idx)
                    if option is not None and option.id:
                        self._textarea.text = option.id
                        self._hide_suggestions()
                        event.stop()
                        return
            if not text:
                event.stop()  # swallow blank Enter
                return
            self._history.append(text)
            self._history_pos = None
            self._textarea.text = ""
            self.post_message(PromptSubmitted(text=text))
            event.stop()
            return

        # Newline-insert keys: let TextArea handle them naturally for
        # ``shift+enter`` / ``ctrl+j``. Textual maps these to the
        # underlying newline insertion.

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # TextArea's cursor is (row, col); convert to a single-int
        # cursor relative to the start of the buffer for slash-token
        # parsing.
        text = self._textarea.text
        try:
            row, col = self._textarea.cursor_location
            cursor = sum(len(l) + 1 for l in text.splitlines()[:row]) + col
        except Exception:
            cursor = len(text)
        self._refresh_suggestions(text, cursor)

    # ---- suggestion plumbing (mirrors single-line) ----
    def _refresh_suggestions(self, text: str, cursor: int) -> None:
        token, _ = _current_slash_token(text[:cursor])
        if token is None:
            self._hide_suggestions()
            return
        partial = token[1:].lower()
        words = self._words_provider() or []
        matches: list[str] = []
        seen: set[str] = set()
        for word in words:
            if not isinstance(word, str) or not word.startswith("/"):
                continue
            name = word[1:]
            key = name.lower()
            if key in seen:
                continue
            if not partial or _fuzzy_match(key, partial):
                seen.add(key)
                matches.append(word)
                if len(matches) >= 12:
                    break
        if not matches:
            self._hide_suggestions()
            return
        self._suggestions.clear_options()
        self._suggestions.add_options(
            [Option(word, id=word) for word in matches]
        )
        self._suggestions.highlighted = 0
        self._suggestions.remove_class("-hidden")

    def _hide_suggestions(self) -> None:
        if not self._suggestions.has_class("-hidden"):
            self._suggestions.add_class("-hidden")
            self._suggestions.clear_options()

    def _refresh_footer(self) -> None:
        if not self._footer.hints:
            self._footer.set_hints(
                [
                    ("/", "commands"),
                    ("Enter", "submit"),
                    ("Shift+Enter", "newline"),
                    ("Ctrl+C", "cancel"),
                ]
            )


def _fuzzy_match(name: str, partial: str) -> bool:
    if name.startswith(partial):
        return True
    i = 0
    for ch in name:
        if ch == partial[i]:
            i += 1
            if i == len(partial):
                return True
    return False


def _current_slash_token(text_before_cursor: str) -> tuple[str | None, int]:
    """Identical to the single-line variant — extracted only for ergonomics."""

    text = text_before_cursor
    if not text:
        return None, 0
    if text.startswith("/"):
        if " " in text:
            return None, 0
        return text, 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch == "/":
            if i > 0 and not text[i - 1].isspace():
                return None, 0
            token = text[i:]
            if " " in token:
                return None, 0
            return token, i
        if ch.isspace():
            return None, 0
    return None, 0


__all__ = ["PromptInputMulti", "PromptSubmitted"]
