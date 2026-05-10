"""Tests for Phase-4 WI-4.1: multi-line ``PromptInputMulti`` widget."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from src.tui.vim_buffer import VimBuffer
from src.tui.widgets.prompt_input_multi import (
    PromptInputMulti,
    PromptSubmitted,
)


class _Harness(App):
    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget
        self._submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        self._submitted.append(message.text)


# ------------------------------------------------------------------
# Basic input + submission
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiline_supports_typing(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_value("hello world")
        await pilot.pause()
        assert pi.value == "hello world"


@pytest.mark.asyncio
async def test_multiline_clear_resets_value(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_value("draft")
        pi.clear()
        await pilot.pause()
        assert pi.value == ""


@pytest.mark.asyncio
async def test_multiline_set_value_with_newlines(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_value("line one\nline two\nline three")
        await pilot.pause()
        assert pi.value == "line one\nline two\nline three"


# ------------------------------------------------------------------
# VimBuffer bridge
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_buffer_round_trips_value(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_value("alpha\nbeta\ngamma")
        await pilot.pause()
        buf = pi.get_buffer()
        assert isinstance(buf, VimBuffer)
        assert buf.text == "alpha\nbeta\ngamma"


@pytest.mark.asyncio
async def test_set_text_and_cursor_applies(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_text_and_cursor("only one line", (0, 5))
        await pilot.pause()
        assert pi.value == "only one line"


# ------------------------------------------------------------------
# Stash persistence
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stash_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=workspace)
    async with _Harness(pi).run_test() as pilot:
        pi.set_value("draft text\nmore text")
        pi.stash_current_draft()
        await pilot.pause()

    pi2 = PromptInputMulti(words_provider=lambda: [], workspace_root=workspace)
    async with _Harness(pi2).run_test() as pilot:
        await pilot.pause()
        assert pi2.stash_notice.has_stash is True
        pi2.recover_stashed_draft()
        assert pi2.value == "draft text\nmore text"


# ------------------------------------------------------------------
# Sub-component external API parity
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_queued_commands_propagates(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_queued_commands(["/foo", "/bar"])
        await pilot.pause()
        assert pi.queued_commands.queue == ("/foo", "/bar")


@pytest.mark.asyncio
async def test_set_footer_hints_propagates(tmp_path: Path) -> None:
    pi = PromptInputMulti(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_footer_hints([("Ctrl+C", "cancel")])
        await pilot.pause()
        assert pi.footer.hints == (("Ctrl+C", "cancel"),)


# ------------------------------------------------------------------
# Slash-token parser shared with the single-line variant
# ------------------------------------------------------------------


def test_slash_token_helper_at_buffer_start() -> None:
    from src.tui.widgets.prompt_input_multi import _current_slash_token

    assert _current_slash_token("/help") == ("/help", 0)


def test_slash_token_helper_after_whitespace() -> None:
    from src.tui.widgets.prompt_input_multi import _current_slash_token

    assert _current_slash_token("hello /he") == ("/he", 6)


def test_slash_token_helper_terminates_at_space() -> None:
    from src.tui.widgets.prompt_input_multi import _current_slash_token

    assert _current_slash_token("/help me") == (None, 0)


def test_slash_token_helper_rejects_mid_word_slash() -> None:
    from src.tui.widgets.prompt_input_multi import _current_slash_token

    assert _current_slash_token("foo/bar") == (None, 0)
