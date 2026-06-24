"""Unit + widget tests for bracketed-paste handling in :class:`PromptInput`.

Chapter 14 round 2 gap: the ``isPasted`` discriminator (TS ``ParsedKey``)
must survive into the Python port. Today Textual already classifies the
inbound escape sequence as a single :class:`textual.events.Paste`; this
test suite locks the contract that :class:`PromptInput` routes that
event into a single atomic ``handle_paste`` call that bypasses the
slash-popup recomputer, the history pointer, and the vim chord tracker.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual import events
from textual.app import App, ComposeResult

from src.tui.messages import PromptPasted
from src.tui.paste import (
    IMAGE_EXTENSIONS,
    PASTE_THRESHOLD,
    PasteInfo,
    classify_paste,
    detect_image_drag,
)
from src.tui.widgets.prompt_input import PromptInput


# ---- pure-function tests --------------------------------------------------


def test_paste_threshold_is_positive_int():
    """Threshold is a guard against false-positive paste detection."""

    assert isinstance(PASTE_THRESHOLD, int)
    assert PASTE_THRESHOLD > 0


def test_image_extensions_includes_common_formats():
    """The recognised-extensions set must cover the obvious cases."""

    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        assert ext in IMAGE_EXTENSIONS


def test_classify_paste_text_only():
    info = classify_paste("hello world from the clipboard")
    assert info.text == "hello world from the clipboard"
    assert info.length == len("hello world from the clipboard")
    assert info.is_empty is False
    assert info.is_image_drag is False
    assert info.line_count == 1


def test_classify_paste_multiline_counts_lines():
    info = classify_paste("line1\nline2\nline3")
    assert info.line_count == 3
    assert info.is_empty is False


def test_classify_paste_trailing_newline_counted():
    info = classify_paste("hello\n")
    # A trailing ``\n`` means two lines: "hello" + the empty trailing line.
    assert info.line_count == 2


def test_classify_paste_empty():
    info = classify_paste("")
    assert info.is_empty is True
    assert info.length == 0
    assert info.is_image_drag is False
    assert info.line_count == 0


def test_classify_paste_is_frozen():
    info = classify_paste("x")
    with pytest.raises(Exception):
        info.text = "y"  # type: ignore[misc]


def test_detect_image_drag_unix_path():
    assert detect_image_drag("/Users/me/foo.png") is True


def test_detect_image_drag_windows_path():
    assert detect_image_drag(r"C:\Users\me\bar.jpg") is True


def test_detect_image_drag_space_prefix():
    # Drag often arrives with a leading space (terminal artifact).
    assert detect_image_drag(" /tmp/a.png") is True


def test_detect_image_drag_newline_separated_drops():
    payload = "/tmp/a.png\n/tmp/b.jpg"
    assert detect_image_drag(payload) is True


def test_detect_image_drag_two_files_space_separated():
    payload = "/tmp/a.png /tmp/b.jpg"
    assert detect_image_drag(payload) is True


def test_detect_image_drag_negative_plain_text():
    assert detect_image_drag("just regular text") is False


def test_detect_image_drag_negative_url():
    # URLs share the slash prefix but lack a path-extension pattern.
    assert detect_image_drag("https://example.com/foo") is False


def test_detect_image_drag_negative_unknown_extension():
    assert detect_image_drag("/tmp/notes.txt") is False


def test_detect_image_drag_empty_string():
    assert detect_image_drag("") is False


def test_detect_image_drag_case_insensitive_extension():
    # macOS Preview sometimes hands you UPPERCASE extensions.
    assert detect_image_drag("/tmp/SHOT.PNG") is True


def test_detect_image_drag_requires_absolute_path():
    # A bare filename without a path should not trigger image-drag detection
    # because the host has nowhere to read the file from.
    assert detect_image_drag("foo.png") is False


# ---- widget integration ---------------------------------------------------


class _Host(App):
    def __init__(self, prompt: PromptInput) -> None:
        super().__init__()
        self._prompt = prompt
        self.paste_events: list[PromptPasted] = []

    def compose(self) -> ComposeResult:
        yield self._prompt

    def on_prompt_pasted(self, message: PromptPasted) -> None:
        self.paste_events.append(message)


def _make_prompt(vim_mode: bool = False) -> PromptInput:
    return PromptInput(
        words_provider=lambda: ["/help", "/exit", "/repl"],
        vim_mode=vim_mode,
    )


@pytest.mark.asyncio
async def test_handle_paste_inserts_text_atomically():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        info = prompt.handle_paste("hello pasted world")
        await pilot.pause()
        assert info.length == len("hello pasted world")
        assert info.is_image_drag is False
        # The buffer contains the whole payload (Textual would have
        # truncated to first line under the stock _on_paste).
        assert prompt._input.value == "hello pasted world"
        assert prompt._input.cursor_position == len("hello pasted world")
        assert prompt.last_paste is not None
        assert prompt.last_paste.text == "hello pasted world"


@pytest.mark.asyncio
async def test_handle_paste_multiline_preserved():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("line1\nline2\nline3")
        await pilot.pause()
        # Stock Input._on_paste would have kept only "line1". A 3-line paste
        # now shows a placeholder in the (single-line) buffer, but the whole
        # multi-line payload survives intact in the blob store and is
        # restored by expand_pastes — i.e. it is NOT truncated.
        assert prompt._input.value == "[Pasted text #1 +2 lines]"
        assert prompt._pasted_blobs[1] == "line1\nline2\nline3"
        assert prompt.expand_pastes(prompt._input.value) == "line1\nline2\nline3"
        assert prompt.last_paste is not None
        assert prompt.last_paste.line_count == 3


@pytest.mark.asyncio
async def test_paste_does_not_advance_history_pointer():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        # Prime the history with a prior submission.
        prompt._history.append("earlier prompt")
        prompt._history_pos = None
        prompt.handle_paste("pasted content\n")
        await pilot.pause()
        # Paste must not touch history navigation state.
        assert prompt._history_pos is None
        assert prompt._history == ["earlier prompt"]


@pytest.mark.asyncio
async def test_paste_hides_slash_suggestions():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        # Pretend the popup was open just before the paste landed.
        prompt._suggestions.remove_class("-hidden")
        prompt.handle_paste("paste body")
        await pilot.pause()
        assert prompt._suggestions.has_class("-hidden")


@pytest.mark.asyncio
async def test_paste_with_vim_mode_bypasses_chord_tracker():
    """Pasted ``dd`` must NOT fire the ``delete-line`` chord.

    Mirrors chapter 14's "pasted ``\\x03`` should not be Ctrl+C" rule.
    ``dd`` is the simplest vim chord the port supports; the test
    guarantees that a paste containing ``dd`` lands as literal text
    rather than triggering the line-delete action.
    """

    prompt = _make_prompt(vim_mode=True)
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        # Drive the state machine into NORMAL by feeding Escape.
        prompt._vim.handle("escape")
        prompt.handle_paste("dd")
        await pilot.pause()
        # Buffer received the literal two characters.
        assert prompt._input.value == "dd"
        # Vim chord buffer should be untouched (paste path bypasses it).
        assert prompt._vim._pending == ""


@pytest.mark.asyncio
async def test_paste_posts_prompt_pasted_message():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("payload")
        await pilot.pause()
        assert len(host.paste_events) == 1
        assert isinstance(host.paste_events[0].info, PasteInfo)
        assert host.paste_events[0].info.text == "payload"


@pytest.mark.asyncio
async def test_empty_paste_signals_image_clipboard_check():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("")
        await pilot.pause()
        assert prompt._input.value == ""
        assert prompt.last_paste is not None
        assert prompt.last_paste.is_empty is True
        # Host still gets a message so it can poke the clipboard.
        assert len(host.paste_events) == 1
        assert host.paste_events[0].info.is_empty is True


@pytest.mark.asyncio
async def test_image_drag_paste_flags_payload():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("/Users/me/screenshot.png")
        await pilot.pause()
        assert prompt.last_paste is not None
        assert prompt.last_paste.is_image_drag is True
        # The path string is still inserted — image attaching is a
        # follow-up round; the metadata gives the host a chance to
        # offer the user an attach action instead.
        assert prompt._input.value == "/Users/me/screenshot.png"


@pytest.mark.asyncio
async def test_paste_routes_through_subclassed_input():
    """An ``events.Paste`` posted to ``_input`` is intercepted by our subclass."""

    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt._input.focus()
        # Fire the Paste event directly at the input. The custom
        # _on_paste handler should walk up to PromptInput and route
        # through handle_paste. A 3-line paste shows a placeholder, but the
        # full payload is preserved in the blob (stock Input would truncate
        # to the first line — verify routing happened and nothing was lost).
        prompt._input.post_message(events.Paste(text="multi\nline\npaste"))
        await pilot.pause()
        assert prompt._input.value == "[Pasted text #1 +2 lines]"
        assert prompt._pasted_blobs[1] == "multi\nline\npaste"
        assert prompt.last_paste is not None
        assert prompt.last_paste.line_count == 3
        # And the host received the bubbled-up message.
        assert any(
            evt.info.text == "multi\nline\npaste" for evt in host.paste_events
        )


@pytest.mark.asyncio
async def test_handle_paste_inserts_at_cursor():
    """Cursor in the middle of existing text → paste splices, not appends."""

    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt._input.value = "abcXYZ"
        prompt._input.cursor_position = 3
        prompt.handle_paste("-PASTED-")
        await pilot.pause()
        assert prompt._input.value == "abc-PASTED-XYZ"
        assert prompt._input.cursor_position == 3 + len("-PASTED-")
