"""Large-paste placeholder (TUI UX PR 8).

A big paste (> 800 chars or >= 2 newlines / 3+ lines) is replaced in the
single-line input by a "[Pasted text #id +N lines]" placeholder (TS format)
and expanded back to the real text on submit, so a multi-thousand-line/char
paste doesn't flood the buffer. Small pastes are still inserted literally.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.prompt_input import PromptInput, PromptSubmitted


class _Host(App):
    def __init__(self, prompt: PromptInput) -> None:
        super().__init__()
        self._prompt = prompt
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield self._prompt

    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        self.submitted.append(message.text)


def _make_prompt() -> PromptInput:
    return PromptInput(words_provider=lambda: [])


@pytest.mark.asyncio
async def test_small_paste_inserted_literally():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("short paste")
        await pilot.pause()
        assert prompt.current_text() == "short paste"
        assert prompt._pasted_blobs == {}


@pytest.mark.asyncio
async def test_two_line_paste_not_placeholdered():
    # A trailing-newline / 2-line paste stays literal (1 newline < threshold).
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("one\n")  # 1 newline
        await pilot.pause()
        assert "Pasted text" not in prompt.current_text()
        assert prompt._pasted_blobs == {}


@pytest.mark.asyncio
async def test_multiline_paste_becomes_placeholder():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("a\nb\nc\nd")  # 3 newlines
        await pilot.pause()
        # TS label: "+N lines" where N = newline count.
        assert prompt.current_text() == "[Pasted text #1 +3 lines]"
        assert prompt._pasted_blobs[1] == "a\nb\nc\nd"


@pytest.mark.asyncio
async def test_long_single_line_paste_becomes_placeholder():
    prompt = _make_prompt()
    blob = "x" * 900  # > 800 chars, 0 newlines
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste(blob)
        await pilot.pause()
        # Single-line large paste → no "+lines" suffix (TS format).
        assert prompt.current_text() == "[Pasted text #1]"
        assert prompt._pasted_blobs[1] == blob


@pytest.mark.asyncio
async def test_moderate_single_line_paste_stays_literal():
    # 100 chars is below the 800-char threshold → inserted literally
    # (matches TS, which keeps single-line pastes inline until 800).
    prompt = _make_prompt()
    blob = "x" * 100
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste(blob)
        await pilot.pause()
        assert prompt.current_text() == blob
        assert prompt._pasted_blobs == {}


@pytest.mark.asyncio
async def test_submit_expands_placeholder_to_real_text():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("a\nb\nc\nd")
        await pilot.pause()
        assert "[Pasted text #1 +3 lines]" in prompt.current_text()
        await pilot.press("enter")
        await pilot.pause()
        # The agent receives the real multi-line text, not the placeholder.
        assert host.submitted == ["a\nb\nc\nd"]
        assert prompt.current_text() == ""
        assert prompt._pasted_blobs == {}  # cleared after submit


@pytest.mark.asyncio
async def test_placeholder_spliced_midline_and_expanded():
    prompt = _make_prompt()
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        for ch in "see ":
            await pilot.press("space" if ch == " " else ch)
        prompt.handle_paste("L1\nL2\nL3")  # 2 newlines
        await pilot.pause()
        assert prompt.current_text() == "see [Pasted text #1 +2 lines]"
        await pilot.press("enter")
        await pilot.pause()
        assert host.submitted == ["see L1\nL2\nL3"]


@pytest.mark.asyncio
async def test_vim_submit_also_expands_placeholder():
    # The vim-mode submit action shares the expand+clear logic.
    prompt = PromptInput(words_provider=lambda: [], vim_mode=True)
    host = _Host(prompt)
    async with host.run_test() as pilot:
        await pilot.pause()
        prompt.handle_paste("v1\nv2\nv3")
        await pilot.pause()
        assert "[Pasted text #1 +2 lines]" in prompt.current_text()
        prompt._apply_vim_action("submit")  # vim Enter in normal mode
        await pilot.pause()
        assert host.submitted == ["v1\nv2\nv3"]
        assert prompt._pasted_blobs == {}
