"""Unit tests for :class:`SessionPreview` and :class:`RemoteSessionProgressLine`."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.session_preview import (
    RemoteSessionProgressLine,
    SessionPreview,
)


def _renderable_text(widget) -> str:
    rendered = widget.content
    if hasattr(rendered, "plain"):
        return rendered.plain
    return str(rendered)


def test_session_preview_flattens_string_content():
    preview = SessionPreview(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
    )
    text = _renderable_text(preview)
    assert "hello" in text
    assert "world" in text
    assert "❯" in text
    assert "☞" in text


def test_session_preview_handles_block_content():
    preview = SessionPreview(
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking out loud"},
                    {"type": "tool_use", "name": "Bash"},
                ],
            }
        ]
    )
    text = _renderable_text(preview)
    assert "thinking out loud" in text
    assert "[tool_use: Bash]" in text


def test_session_preview_skips_empty_messages():
    preview = SessionPreview(
        messages=[
            {"role": "user", "content": ""},
            {"role": "system", "content": None},
            {"role": "user", "content": "hi"},
        ]
    )
    text = _renderable_text(preview)
    assert text.count("hi") == 1
    assert text.count("❯") == 1


@pytest.mark.asyncio
async def test_session_preview_set_messages_refreshes():
    preview = SessionPreview(messages=[{"role": "user", "content": "first"}])

    class _App(App):
        def compose(self) -> ComposeResult:
            yield preview

    async with _App().run_test() as pilot:
        await pilot.pause()
        preview.set_messages([{"role": "user", "content": "second"}])
        await pilot.pause()
        text = _renderable_text(preview)
        assert "second" in text
        assert "first" not in text


@pytest.mark.asyncio
async def test_remote_progress_line_tick_rotates_spinner():
    line = RemoteSessionProgressLine(
        title="delegation",
        started_at=time.time() - 5,
        step=2,
    )

    class _App(App):
        def compose(self) -> ComposeResult:
            yield line

    async with _App().run_test() as pilot:
        await pilot.pause()
        initial = _renderable_text(line)
        line.tick()
        await pilot.pause()
        after = _renderable_text(line)
        assert after != initial
        assert "delegation" in after
        assert " step " in after
        assert "2" in after


@pytest.mark.asyncio
async def test_remote_progress_line_set_step():
    line = RemoteSessionProgressLine(title="x", started_at=time.time(), step=1)

    class _App(App):
        def compose(self) -> ComposeResult:
            yield line

    async with _App().run_test() as pilot:
        await pilot.pause()
        line.set_step(7)
        await pilot.pause()
        text = _renderable_text(line)
        assert "7" in text
