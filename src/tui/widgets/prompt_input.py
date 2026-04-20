"""Prompt input widget — bottom ``❯`` line with slash palette + history.

Port of ``typescript/src/components/PromptInput/PromptInput.tsx`` at the
fidelity required to feel like the ink reference:

* Multi-line editing via ``Shift+Enter`` / ``Ctrl+J`` (Textual's default)
  with plain ``Enter`` submitting the prompt.
* Slash-command palette opens when the current token starts with ``/``
  and fuzzy-filters as the user types.
* Up / Down navigate the in-session history when the palette is closed;
  when it is open, arrow keys drive the option list.
* ``Escape`` closes the palette without losing the draft.
* ``Ctrl+L`` clears the draft.

Phase 1 deliberately keeps the implementation single-line-in-practice
(using Textual's ``Input``) but exposes :meth:`set_multiline` for
Phase 2 to swap in a ``TextArea`` without changing the public surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ..messages import CancelRequested
from ..vim import VimState


@dataclass
class PromptSubmitted(Message):
    """User pressed Enter on a non-empty prompt."""

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


class PromptInput(Vertical):
    """Input line plus slash-command suggestion popup."""

    DEFAULT_CSS = """
    PromptInput {
        height: auto;
        padding: 0 0;
    }
    PromptInput > Input {
        border: round $primary-darken-2;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("ctrl+l", "clear_draft", "Clear draft"),
    ]

    def __init__(
        self,
        *,
        words_provider: Callable[[], list[str]],
        vim_mode: bool = False,
    ) -> None:
        super().__init__()
        self._words_provider = words_provider
        self._history: list[str] = []
        self._history_pos: int | None = None
        self._input = Input(placeholder="Type a prompt, or / for commands")
        self._suggestions = _SlashSuggestions(classes="-hidden")
        self._vim = VimState(enabled=vim_mode)
        self._yank_buffer: str = ""

    def compose(self) -> ComposeResult:
        yield self._input
        yield self._suggestions

    def on_mount(self) -> None:
        self._input.focus()

    # ---- external API ----
    def focus_input(self) -> None:
        self._input.focus()

    def clear(self) -> None:
        self._input.value = ""
        self._hide_suggestions()

    def set_value(self, value: str) -> None:
        """Replace the draft text in the prompt (used by /history)."""

        self._input.value = value or ""
        self._hide_suggestions()

    def action_clear_draft(self) -> None:
        self.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable / disable the input (used when a modal steals focus)."""

        self._input.disabled = not enabled
        if enabled:
            self._input.focus()

    # ---- vim mode ----
    def set_vim_mode(self, enabled: bool) -> None:
        """Toggle vim-mode on the prompt."""

        self._vim.set_enabled(enabled)

    @property
    def vim_mode(self) -> bool:
        return self._vim.enabled

    @property
    def vim_state(self) -> VimState:  # exposed for tests / status line
        return self._vim

    # ---- input events ----
    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_suggestions(event.value, event.input.cursor_position)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        # If the palette is open and a row is highlighted, accept the
        # selection instead of submitting the partial prompt.
        if not self._suggestions.has_class("-hidden"):
            idx = self._suggestions.highlighted
            if idx is not None:
                option = self._suggestions.get_option_at_index(idx)
                if option is not None and option.id:
                    self._input.value = option.id
                    self._input.cursor_position = len(option.id)
                    self._hide_suggestions()
                    return
        self._history.append(text)
        self._history_pos = None
        self._hide_suggestions()
        self._input.value = ""
        self.post_message(PromptSubmitted(text=text))

    async def on_key(self, event: events.Key) -> None:
        key = event.key

        # Vim mode: consume chord-owned keys before the Input sees them.
        if self._vim.enabled:
            result = self._vim.handle(key)
            if result.consumed:
                if result.action is not None:
                    self._apply_vim_action(result.action)
                event.stop()
                return

        if key == "escape" and not self._suggestions.has_class("-hidden"):
            self._hide_suggestions()
            event.stop()
            return
        if key == "escape":
            # Bubble up to the app; it decides whether to actually
            # cancel based on whether the agent bridge is busy.
            # Mirrors the TS reference's chat:cancel keybinding.
            self.post_message(CancelRequested())
            event.stop()
            return
        if key in ("up", "down"):
            if not self._suggestions.has_class("-hidden"):
                self._suggestions.focus()
                if key == "up":
                    self._suggestions.action_cursor_up()
                else:
                    self._suggestions.action_cursor_down()
                event.stop()
                return
            self._navigate_history(1 if key == "up" else -1)
            event.stop()
            return

    # ---- vim action application ----
    def _apply_vim_action(self, action: str) -> None:
        inp = self._input
        value = inp.value or ""
        pos = inp.cursor_position
        if action == "insert-before":
            return
        if action == "insert-after":
            inp.cursor_position = min(len(value), pos + 1)
        elif action == "insert-line-start":
            inp.cursor_position = 0
        elif action == "insert-line-end":
            inp.cursor_position = len(value)
        elif action == "move-left":
            inp.cursor_position = max(0, pos - 1)
        elif action == "move-right":
            inp.cursor_position = min(len(value), pos + 1)
        elif action == "move-start":
            inp.cursor_position = 0
        elif action == "move-end":
            inp.cursor_position = len(value)
        elif action == "move-word-next":
            inp.cursor_position = _next_word(value, pos)
        elif action == "move-word-prev":
            inp.cursor_position = _prev_word(value, pos)
        elif action == "delete-char":
            if pos < len(value):
                inp.value = value[:pos] + value[pos + 1 :]
        elif action == "delete-line":
            self._yank_buffer = value
            inp.value = ""
        elif action == "yank-line":
            self._yank_buffer = value
        elif action == "paste-after":
            if self._yank_buffer:
                inp.value = value[: pos + 1] + self._yank_buffer + value[pos + 1 :]
                inp.cursor_position = pos + 1 + len(self._yank_buffer)
        elif action == "paste-before":
            if self._yank_buffer:
                inp.value = value[:pos] + self._yank_buffer + value[pos:]
                inp.cursor_position = pos + len(self._yank_buffer)
        elif action == "submit":
            text = value.strip()
            if text:
                self._history.append(text)
                self._history_pos = None
                inp.value = ""
                self.post_message(PromptSubmitted(text=text))

    # ---- suggestion plumbing ----
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
        self._suggestions.add_options([Option(word, id=word) for word in matches])
        self._suggestions.highlighted = 0
        self._suggestions.remove_class("-hidden")

    def _hide_suggestions(self) -> None:
        if not self._suggestions.has_class("-hidden"):
            self._suggestions.add_class("-hidden")
            self._suggestions.clear_options()

    def _navigate_history(self, direction: int) -> None:
        """``direction`` = +1 means older (Up); -1 means newer (Down)."""
        if not self._history:
            return
        if direction > 0:
            if self._history_pos is None:
                self._history_pos = len(self._history) - 1
            else:
                self._history_pos = max(0, self._history_pos - 1)
        else:
            if self._history_pos is None:
                return
            self._history_pos += 1
            if self._history_pos >= len(self._history):
                self._history_pos = None
                self._input.value = ""
                return
        self._input.value = self._history[self._history_pos]
        self._input.cursor_position = len(self._input.value)


def _fuzzy_match(name: str, partial: str) -> bool:
    """Lightweight fuzzy matcher: prefix wins, subsequence falls back.

    Matches the behavior of ``useTypeahead`` in
    ``typescript/src/components/PromptInput/useTypeahead.ts`` at a
    reduced fidelity (no scoring, no MRU). Prefix matches are always
    preferred so the most common ``/ex<Tab>`` workflow feels snappy.
    """

    if name.startswith(partial):
        return True
    i = 0
    for ch in name:
        if ch == partial[i]:
            i += 1
            if i == len(partial):
                return True
    return False


def _next_word(text: str, pos: int) -> int:
    """Return the cursor index of the next word start.

    ``pos`` is clamped to ``[0, len(text)]``. A word is any run of
    non-whitespace characters; we skip the current word first, then
    any whitespace.
    """

    n = len(text)
    pos = max(0, min(pos, n))
    # skip to end of current word
    while pos < n and not text[pos].isspace():
        pos += 1
    # skip intervening whitespace
    while pos < n and text[pos].isspace():
        pos += 1
    return pos


def _prev_word(text: str, pos: int) -> int:
    """Return the cursor index of the previous word start."""

    pos = max(0, min(pos, len(text)))
    # step back over whitespace
    while pos > 0 and text[pos - 1].isspace():
        pos -= 1
    # step back to start of word
    while pos > 0 and not text[pos - 1].isspace():
        pos -= 1
    return pos


def _current_slash_token(text_before_cursor: str) -> tuple[str | None, int]:
    """Return ``(token, start_idx)`` for the slash command under the cursor.

    Semantics locked in by :mod:`tests.tui.test_slash_token_parser`: a
    slash token is a ``/word`` that either starts at the beginning of
    the buffer or is preceded by whitespace. A slash followed by a
    space has already been "committed" and does not re-open the popup.
    """

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
