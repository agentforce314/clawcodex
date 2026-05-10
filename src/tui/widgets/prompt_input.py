"""Prompt input widget — bottom ``❯`` line with slash palette + history.

Port of ``typescript/src/components/PromptInput/PromptInput.tsx`` at the
fidelity required to feel like the ink reference:

* Multi-line editing via ``Shift+Enter`` / ``Ctrl+J`` (Textual's default)
  with plain ``Enter`` submitting the prompt.
* Slash-command palette opens when the current token starts with ``/``
  and fuzzy-filters as the user types.
* ``@``-file completion popup opens when the current token starts with
  ``@`` (Phase-3 WI-3.2; mirrors the slash popup pattern).
* Up / Down navigate the in-session history when neither palette is open;
  when one is open, arrow keys drive its option list.
* ``Escape`` closes whichever palette is open without losing the draft.
* ``Ctrl+L`` clears the draft.

Phase-3 sub-components (mode indicator / footer / queued commands /
stash notice) are mounted *as siblings* of the input so the host screen
can layer them; see the ``Vertical`` ``compose()`` body. The help-menu
overlay is a :class:`ModalScreen` and is launched from the host screen,
not by this widget directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TYPE_CHECKING

from prompt_toolkit.document import Document as _Document
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ..declared_cursor import flush_pending, publish_cursor_position
from ..messages import CancelRequested
from ..vim import VimState
from src.utils.at_file_completer import AtFileCompleter as _AtFileCompleter
from .prompt_input_footer import PromptInputFooter
from .prompt_input_mode_indicator import PromptInputModeIndicator
from .prompt_input_queued_commands import PromptInputQueuedCommands
from .prompt_input_stash_notice import (
    PromptInputStashNotice,
    read_stash,
    write_stash,
    clear_stash,
)

if TYPE_CHECKING:  # pragma: no cover
    from src.utils.at_file_completer import AtFileCompleter


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


class _AtFileSuggestions(OptionList):
    """``@``-file completion popup. Sibling to ``_SlashSuggestions``."""

    DEFAULT_CSS = """
    _AtFileSuggestions {
        max-height: 10;
        border: round $accent;
        background: $surface;
    }
    _AtFileSuggestions.-hidden {
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
        workspace_root: Path | None = None,
    ) -> None:
        super().__init__()
        self._words_provider = words_provider
        self._history: list[str] = []
        self._history_pos: int | None = None
        self._input = Input(placeholder="Type a prompt, or / for commands")
        self._suggestions = _SlashSuggestions(classes="-hidden")
        self._at_suggestions = _AtFileSuggestions(classes="-hidden")
        self._vim = VimState(enabled=vim_mode)
        self._yank_buffer: str = ""
        self._workspace_root = workspace_root or Path.cwd()
        # Phase-3 sub-component widgets — composed in ``compose()``. Each
        # auto-hides until its content lands.
        self._mode_indicator = PromptInputModeIndicator()
        self._footer = PromptInputFooter()
        self._queued_commands = PromptInputQueuedCommands()
        self._stash_notice = PromptInputStashNotice()
        # Lazily-built ``AtFileCompleter`` shared with the legacy REPL via
        # the WI-3.1 extracted module. We don't construct it eagerly so
        # tests that don't exercise ``@`` paths skip the I/O cost.
        self._at_completer: "AtFileCompleter | None" = None

    def compose(self) -> ComposeResult:
        # Order: stash notice (if any) → queued commands → mode indicator →
        # input → slash popup → at-file popup → footer. Auto-hidden
        # widgets render zero-height so the layout collapses naturally.
        yield self._stash_notice
        yield self._queued_commands
        yield self._mode_indicator
        yield self._input
        yield self._suggestions
        yield self._at_suggestions
        yield self._footer

    def on_mount(self) -> None:
        self._input.focus()
        # Surface any stash from a prior session.
        try:
            stashed = read_stash(self._workspace_root)
        except Exception:
            stashed = ""
        self._stash_notice.announce_stash(bool(stashed and stashed.strip()))
        self._refresh_mode_indicator()
        self._refresh_footer()

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

    # ---- queued commands / stash external API (Phase 3) ----
    def set_queued_commands(self, queue: Iterable[str]) -> None:
        """Update the chip row showing slash commands waiting to fire."""

        self._queued_commands.set_queue(tuple(queue))

    def set_footer_hints(self, hints: Iterable[tuple[str, str]]) -> None:
        """Update the footer's keybinding hint set.

        ``hints`` is an iterable of ``(key, label)`` pairs, e.g.
        ``[("Ctrl+C", "cancel"), ("Esc", "close")]``. Empty hides the row.
        """

        self._footer.set_hints(hints)

    def stash_current_draft(self) -> None:
        """Persist the current draft as a stash. Called at exit time."""

        text = self._input.value or ""
        if text.strip():
            write_stash(text, self._workspace_root)

    def recover_stashed_draft(self) -> bool:
        """Replace the input value with the stash; clear the stash file.

        Returns ``True`` iff there was a stash to recover.
        """

        text = read_stash(self._workspace_root)
        if not text or not text.strip():
            self._stash_notice.announce_stash(False)
            return False
        self._input.value = text
        self._input.cursor_position = len(text)
        clear_stash(self._workspace_root)
        self._stash_notice.announce_stash(False)
        return True

    @property
    def stash_notice(self) -> PromptInputStashNotice:  # test seam
        return self._stash_notice

    @property
    def queued_commands(self) -> PromptInputQueuedCommands:  # test seam
        return self._queued_commands

    @property
    def footer(self) -> PromptInputFooter:  # test seam
        return self._footer

    @property
    def mode_indicator(self) -> PromptInputModeIndicator:  # test seam
        return self._mode_indicator

    # ---- vim mode ----
    def set_vim_mode(self, enabled: bool) -> None:
        """Toggle vim-mode on the prompt."""

        self._vim.set_enabled(enabled)
        self._refresh_mode_indicator()

    @property
    def vim_mode(self) -> bool:
        return self._vim.enabled

    @property
    def vim_state(self) -> VimState:  # exposed for tests / status line
        return self._vim

    def _publish_caret(self) -> None:
        """Tell the host terminal where the IME preedit cursor should land.

        Phase-6 wiring (gap #3): emit a CSI cursor-position sequence so
        CJK / IME users see preedit text under the input caret rather
        than at the bottom of the screen. Best-effort — the helper
        swallows non-interactive failures, and tests can disable
        emission via ``CLAWCODEX_DISABLE_DECLARED_CURSOR=1``.
        """

        try:
            row = self._input.region.y
            col = self._input.region.x + (self._input.cursor_position or 0)
        except Exception:
            return
        publish_cursor_position(self, row, col)
        flush_pending()

    # ---- input events ----
    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        cursor = event.input.cursor_position
        self._publish_caret()
        # Two completer popups, mutually exclusive: ``@``-file completion
        # wins when both regexes could match, since slash tokens never
        # contain ``@``.
        at_token, _ = _current_at_token(text[:cursor])
        if at_token is not None:
            self._hide_suggestions()
            self._refresh_at_suggestions(text, cursor)
        else:
            self._hide_at_suggestions()
            self._refresh_suggestions(text, cursor)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        # If a palette is open and a row is highlighted, accept the
        # selection instead of submitting the partial prompt. ``@``-file
        # popup uses a different replacement strategy (it inserts only
        # the matched fragment, not the whole input).
        if not self._suggestions.has_class("-hidden"):
            idx = self._suggestions.highlighted
            if idx is not None:
                option = self._suggestions.get_option_at_index(idx)
                if option is not None and option.id:
                    self._input.value = option.id
                    self._input.cursor_position = len(option.id)
                    self._hide_suggestions()
                    return
        if not self._at_suggestions.has_class("-hidden"):
            idx = self._at_suggestions.highlighted
            if idx is not None:
                option = self._at_suggestions.get_option_at_index(idx)
                if option is not None and option.id:
                    self._accept_at_selection(option.id)
                    return
        self._history.append(text)
        self._history_pos = None
        self._hide_suggestions()
        self._hide_at_suggestions()
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
        if key == "escape" and not self._at_suggestions.has_class("-hidden"):
            self._hide_at_suggestions()
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
            popup = self._active_popup()
            if popup is not None:
                popup.focus()
                if key == "up":
                    popup.action_cursor_up()
                else:
                    popup.action_cursor_down()
                event.stop()
                return
            self._navigate_history(1 if key == "up" else -1)
            event.stop()
            return
        if key == "ctrl+r":
            # Recover the stash if one is shown — non-blocking; if no
            # stash is present the call is a no-op and the keystroke
            # falls through to the default Input behavior.
            if self._stash_notice.has_stash:
                self.recover_stashed_draft()
                event.stop()
                return

    # ---- sub-component refresh helpers ----
    def _refresh_mode_indicator(self) -> None:
        """Reflect the vim state into the mode-indicator widget."""

        mode = "insert"
        if self._vim.enabled:
            mode = (getattr(self._vim, "mode", None) or "insert").lower()
        self._mode_indicator.set_state(enabled=self._vim.enabled, mode=mode)

    def _refresh_footer(self) -> None:
        """Default hint set; host screens override via :meth:`set_footer_hints`."""

        if not self._footer.hints:
            self._footer.set_hints(
                [
                    ("/", "commands"),
                    ("@", "file"),
                    ("Ctrl+C", "cancel"),
                    ("?", "help"),
                ]
            )

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

    def _hide_at_suggestions(self) -> None:
        if not self._at_suggestions.has_class("-hidden"):
            self._at_suggestions.add_class("-hidden")
            self._at_suggestions.clear_options()

    def _active_popup(self) -> OptionList | None:
        if not self._suggestions.has_class("-hidden"):
            return self._suggestions
        if not self._at_suggestions.has_class("-hidden"):
            return self._at_suggestions
        return None

    # ---- @-file completion ----
    def _ensure_at_completer(self):
        """Construct the shared :class:`AtFileCompleter` lazily.

        Constructed against ``self._workspace_root`` so the project-file
        index (``git ls-files`` / filesystem walk) is rooted correctly
        and per-workspace cached.
        """

        if self._at_completer is None:
            self._at_completer = _AtFileCompleter(self._workspace_root)
        return self._at_completer

    def _refresh_at_suggestions(self, text: str, cursor: int) -> None:
        token, start = _current_at_token(text[:cursor])
        if token is None:
            self._hide_at_suggestions()
            return

        # Build a prompt_toolkit ``Document`` so we can reuse
        # :class:`AtFileCompleter` — the same completer the legacy REPL
        # uses, which handles git-ls-files / filesystem walk + the
        # ``@/...`` path-like fast-path. Calling ``_path_completions``
        # directly (the previous shape) silently returned empty for
        # project-file tokens like ``@src/tu...`` — the headline use
        # case.
        try:
            doc = _Document(text=text[:cursor], cursor_position=cursor)
        except Exception:
            self._hide_at_suggestions()
            return

        try:
            completer = self._ensure_at_completer()
            results = list(completer.get_completions(doc, None))
        except Exception:
            self._hide_at_suggestions()
            return

        if not results:
            self._hide_at_suggestions()
            return

        self._at_suggestions.clear_options()
        added = 0
        for completion in results:
            label = getattr(completion, "display", None) or getattr(
                completion, "text", None
            )
            if not label:
                continue
            # Convert Rich/prompt_toolkit display objects to a plain str
            # since OptionList wants string IDs and labels.
            label_str = (
                label if isinstance(label, str) else getattr(label, "text", str(label))
            )
            value = getattr(completion, "text", None)
            if not value:
                continue
            start_offset = int(getattr(completion, "start_position", 0))
            # Encode both replacement text AND start_offset in the option
            # id so ``_accept_at_selection`` can splice without re-parsing.
            option_id = f"{start_offset}\0{value}"
            self._at_suggestions.add_option(Option(label_str, id=option_id))
            added += 1
            if added >= 12:
                break
        if not added:
            self._hide_at_suggestions()
            return
        self._at_suggestions.highlighted = 0
        self._at_suggestions.remove_class("-hidden")

    def _accept_at_selection(self, option_id: str) -> None:
        """Splice the selected completion into the input.

        ``option_id`` carries ``"<start_offset>\\0<replacement_text>"``
        (encoded by :meth:`_refresh_at_suggestions`) so we don't
        re-parse the buffer here.
        """

        text = self._input.value or ""
        cursor = self._input.cursor_position
        # Decode the option id; defensive against direct callers that
        # pass a plain string (the splice math falls back to a sensible
        # cursor-relative replacement).
        if "\0" in option_id:
            offset_str, replacement = option_id.split("\0", 1)
            try:
                start_offset = int(offset_str)
            except ValueError:
                start_offset = 0
        else:
            replacement = option_id
            # Plain-string callers: replace the current ``@<token>`` span.
            token, token_start = _current_at_token(text[:cursor])
            start_offset = (token_start - cursor) if token is not None else 0

        replace_start = max(0, cursor + start_offset)
        before = text[:replace_start]
        after = text[cursor:]
        # Keep typing-friendly: append a space unless the cursor already
        # sits before whitespace (avoids the double-space the Critic
        # caught when accepting mid-buffer).
        if after and after[0].isspace():
            tail = after
            cursor_advance = len(replacement)
        else:
            tail = " " + after
            cursor_advance = len(replacement) + 1
        self._input.value = f"{before}{replacement}{tail}"
        self._input.cursor_position = replace_start + cursor_advance
        self._hide_at_suggestions()

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


def _current_at_token(text_before_cursor: str) -> tuple[str | None, int]:
    """Return ``(token, start_idx)`` for the ``@``-file token under the cursor.

    Mirror of :func:`_current_slash_token`. An ``@`` token is ``@`` followed
    by zero or more non-space chars; it must either start at the buffer
    beginning or be preceded by whitespace. A space terminates the token.
    """

    text = text_before_cursor
    if not text:
        return None, 0
    if text.startswith("@"):
        if " " in text:
            return None, 0
        return text, 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch == "@":
            if i > 0 and not text[i - 1].isspace():
                return None, 0
            token = text[i:]
            if " " in token:
                return None, 0
            return token, i
        if ch.isspace():
            return None, 0
    return None, 0


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
