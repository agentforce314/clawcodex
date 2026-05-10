"""Tests for Phase-5 inline transcript search overlay (gap #2)."""

from __future__ import annotations

from rich.text import Text

from src.tui.widgets.transcript_search import (
    TranscriptSearch,
    find_matches,
    _renderable_text,
    _row_text,
)


# ------------------------------------------------------------------
# _renderable_text — robust extraction
# ------------------------------------------------------------------


def test_renderable_text_handles_str() -> None:
    assert _renderable_text("hello") == "hello"


def test_renderable_text_handles_rich_text() -> None:
    assert _renderable_text(Text("styled hello")) == "styled hello"


def test_renderable_text_handles_tuple() -> None:
    assert (
        _renderable_text((Text("a"), "b", Text("c")))
        == "a\nb\nc"
    )


def test_renderable_text_handles_none() -> None:
    assert _renderable_text(None) == ""


# ------------------------------------------------------------------
# _row_text + find_matches
# ------------------------------------------------------------------


class _FakeRow:
    def __init__(self, text: str) -> None:
        self._text = text

    def snapshot(self):
        return Text(self._text)


def test_row_text_uses_snapshot() -> None:
    row = _FakeRow("hello world")
    assert _row_text(row) == "hello world"


def test_row_text_handles_missing_snapshot() -> None:
    class NoSnap:
        def __str__(self) -> str:
            return "fallback"

    assert _row_text(NoSnap()) == "fallback"


def test_row_text_swallows_snapshot_exceptions() -> None:
    class Bad:
        def snapshot(self):
            raise RuntimeError("oops")

    assert _row_text(Bad()) == ""


def test_find_matches_case_insensitive() -> None:
    rows = [
        _FakeRow("Hello World"),
        _FakeRow("nothing here"),
        _FakeRow("hello again"),
    ]
    assert find_matches(rows, "HELLO") == [0, 2]


def test_find_matches_empty_query_returns_empty() -> None:
    rows = [_FakeRow("anything")]
    assert find_matches(rows, "") == []


def test_find_matches_no_hits_returns_empty() -> None:
    rows = [_FakeRow("a"), _FakeRow("b")]
    assert find_matches(rows, "zzz") == []


def test_find_matches_substring_anywhere_in_row() -> None:
    """Match is substring, not whole-line."""

    rows = [_FakeRow("the quick brown fox jumps over")]
    assert find_matches(rows, "fox") == [0]


def test_find_matches_handles_mixed_row_shapes() -> None:
    """A mix of rows with snapshots and rows without should work."""

    class Plain:
        def __str__(self) -> str:
            return "plain string row"

    rows = [_FakeRow("snapshot row foo"), Plain()]
    assert find_matches(rows, "row") == [0, 1]


# ------------------------------------------------------------------
# TranscriptSearch — modal lifecycle smoke tests
# ------------------------------------------------------------------


import pytest
from textual.app import App
from textual.containers import VerticalScroll
from textual.widget import Widget


class _FakeTranscript(VerticalScroll):
    """Stand-in for TranscriptView with the ``_mounted_rows`` attribute."""

    def __init__(self, rows) -> None:
        super().__init__()
        self._mounted_rows = list(rows)


@pytest.mark.asyncio
async def test_transcript_search_opens_and_closes_on_escape() -> None:
    rows = [_FakeRow("alpha"), _FakeRow("beta"), _FakeRow("gamma")]
    transcript = _FakeTranscript(rows)

    class _Harness(App):
        def __init__(self) -> None:
            super().__init__()

        def compose(self):
            yield transcript

        async def on_mount(self) -> None:
            await self.push_screen(TranscriptSearch(transcript))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        # Modal is on the stack.
        assert isinstance(pilot.app.screen, TranscriptSearch)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, TranscriptSearch)


@pytest.mark.asyncio
async def test_transcript_search_filters_on_typing() -> None:
    rows = [
        _FakeRow("alpha"),
        _FakeRow("alphabet"),
        _FakeRow("beta"),
    ]
    transcript = _FakeTranscript(rows)

    class _Harness(App):
        def compose(self):
            yield transcript

        async def on_mount(self) -> None:
            await self.push_screen(TranscriptSearch(transcript))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, TranscriptSearch)
        # Type 'alph' — should match rows 0 and 1.
        from textual.widgets import Input

        input_widget = screen.query_one("#search-input", Input)
        input_widget.value = "alph"
        # The Input.Changed message would normally fire here; trigger
        # the screen's handler directly to keep the test sync.
        screen.on_input_changed(
            type("E", (), {"value": "alph"})()
        )
        await pilot.pause()
        assert screen._matches == [0, 1]


@pytest.mark.asyncio
async def test_transcript_search_navigation_actions() -> None:
    rows = [_FakeRow("alpha"), _FakeRow("alpha2"), _FakeRow("beta")]
    transcript = _FakeTranscript(rows)

    class _Harness(App):
        def compose(self):
            yield transcript

        async def on_mount(self) -> None:
            await self.push_screen(TranscriptSearch(transcript))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, TranscriptSearch)
        screen.search_query = "alph"
        screen._refresh_matches()
        await pilot.pause()
        assert screen._matches == [0, 1]
        screen.action_next_match()
        assert screen.match_index == 1
        screen.action_next_match()
        # Wraps around to 0.
        assert screen.match_index == 0
        screen.action_prev_match()
        assert screen.match_index == 1


@pytest.mark.asyncio
async def test_transcript_search_dismisses_with_row_index_on_submit() -> None:
    rows = [_FakeRow("alpha"), _FakeRow("beta foo"), _FakeRow("gamma")]
    transcript = _FakeTranscript(rows)
    captured: list[int | None] = []

    def _on_dismiss(value: int | None) -> None:
        captured.append(value)

    class _Harness(App):
        def compose(self):
            yield transcript

        async def on_mount(self) -> None:
            self.push_screen(TranscriptSearch(transcript), _on_dismiss)

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, TranscriptSearch)
        screen.search_query = "foo"
        screen._refresh_matches()
        await pilot.pause()
        # Submit the search — should dismiss with row index 1.
        screen.on_input_submitted(type("E", (), {})())
        await pilot.pause()

    assert captured == [1]
