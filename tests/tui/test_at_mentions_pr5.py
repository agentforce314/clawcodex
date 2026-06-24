"""`@` file-mention dropdown (TUI UX PR 5).

Ports the ink file typeahead: typing ``@`` (at the start or after
whitespace) opens an inline file-suggestion dropdown; accepting a row
splices ``@<path> `` into the draft in place. Reuses the ``/search`` file
infra (``workspace_search.list_workspace_files``/``filter_files``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.prompt_input import (
    PromptInput,
    _current_at_token,
    _current_slash_token,
)

_FILES = [
    "src/app.py",
    "src/tui/widgets/prompt_input.py",
    "README.md",
    "tests/test_app.py",
]


# ---- token parser ---------------------------------------------------------


def test_at_token_triggers_at_start_and_after_space():
    assert _current_at_token("@") == ("@", 0)
    assert _current_at_token("@util") == ("@util", 0)
    assert _current_at_token("explain @ut") == ("@ut", 8)


def test_at_token_not_triggered_mid_word_or_after_space():
    # An email-like @ (preceded by non-space) is not a mention trigger.
    assert _current_at_token("email@host") == (None, 0)
    # A committed mention (space after) closes the popup.
    assert _current_at_token("@util ") == (None, 0)
    assert _current_at_token("plain text") == (None, 0)


def test_slash_token_unchanged_by_refactor():
    # The shared parser must preserve slash semantics.
    assert _current_slash_token("/he") == ("/he", 0)
    assert _current_slash_token("echo /ex") == ("/ex", 5)
    assert _current_slash_token("src/re") == (None, 0)


# ---- widget harness -------------------------------------------------------


class _Host(App):
    def __init__(self, prompt: PromptInput) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield self._prompt


def _make_prompt() -> PromptInput:
    return PromptInput(
        words_provider=lambda: [],
        files_provider=lambda: list(_FILES),
    )


def _rows(prompt: PromptInput) -> list[str]:
    sl = prompt._suggestions
    if sl.has_class("-hidden"):
        return []
    return [sl.get_option_at_index(i).id for i in range(sl.option_count)]


@pytest.mark.asyncio
async def test_at_opens_file_dropdown():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("@")
        await pilot.pause()
        # Empty @ lists files (capped).
        assert len(_rows(prompt)) > 0
        assert "README.md" in _rows(prompt)


@pytest.mark.asyncio
async def test_at_filters_fuzzily():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        for ch in "@prompt":
            await pilot.press(ch)
        await pilot.pause()
        rows = _rows(prompt)
        assert "src/tui/widgets/prompt_input.py" in rows
        assert "README.md" not in rows


@pytest.mark.asyncio
async def test_accept_splices_mention_in_place():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        for ch in "explain ":
            await pilot.press("space" if ch == " " else ch)
        for ch in "@read":
            await pilot.press(ch)
        await pilot.pause()
        assert "README.md" in _rows(prompt)
        await pilot.press("enter")  # accept highlighted
        await pilot.pause()
        # Spliced in place, surrounding text preserved, trailing space added.
        assert prompt.current_text() == "explain @README.md "


@pytest.mark.asyncio
async def test_tab_also_splices_file():
    prompt = _make_prompt()
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        for ch in "@readme":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.current_text() == "@README.md "


@pytest.mark.asyncio
async def test_at_disabled_without_files_provider():
    prompt = PromptInput(words_provider=lambda: [])  # no files_provider
    async with _Host(prompt).run_test() as pilot:
        await pilot.pause()
        await pilot.press("@")
        await pilot.pause()
        assert _rows(prompt) == []  # no dropdown
        assert prompt.current_text() == "@"  # @ is a literal char
