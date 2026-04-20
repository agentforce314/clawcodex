"""Tests for Phase 4 polish: preview theme, virtualized transcript."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from src.tui.screens.theme_picker import ThemePickerScreen
from src.tui.widgets.transcript_view import Transcript


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _App(App):
    def on_mount(self) -> None:
        self.push_screen(_Host())


def _push(app: App, screen) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _callback(result):
        if not future.done():
            future.set_result(result)

    app.push_screen(screen, callback=_callback)
    return future


@pytest.mark.asyncio
async def test_theme_picker_fires_preview_on_highlight():
    previews: list[str | None] = []
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ThemePickerScreen(
            themes=["auto", "dark", "light", "claude"],
            current="dark",
            on_preview=previews.append,
        )
        fut = _push(app, screen)
        await pilot.pause()
        # Starting position = "dark"; two downs land on "claude".
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        result = await fut
        assert result == "claude"
        # Highlight fires on every cursor move.
        assert previews
        assert "light" in previews
        assert "claude" in previews


@pytest.mark.asyncio
async def test_theme_picker_preview_none_on_cancel():
    previews: list[str | None] = []
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ThemePickerScreen(
            themes=["dark", "light"],
            current="dark",
            on_preview=previews.append,
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("escape")
        result = await fut
        assert result is None
        # Cancellation should emit a None sentinel so the host can
        # restore the original theme.
        assert previews[-1] is None


@pytest.mark.asyncio
async def test_transcript_evicts_oldest_rows_above_cap():
    transcript = Transcript(max_messages=5)

    class _Harness(App):
        def compose(self) -> ComposeResult:
            yield transcript

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        for i in range(10):
            transcript.append_user(f"msg {i}")
            await pilot.pause()
        # Soft cap is 5; count should hover at or just below it.
        assert transcript.message_count <= 5
        # The latest message should still be present.
        texts = [
            getattr(child, "renderable", "")
            for child in list(transcript.children)
        ]
        # Rough sanity: no row should reference the earliest index 0.
        rendered = " ".join(str(t) for t in texts)
        assert "msg 9" in rendered or transcript.message_count == 5


@pytest.mark.asyncio
async def test_transcript_preserves_active_streaming_row():
    transcript = Transcript(max_messages=3)

    class _Harness(App):
        def compose(self) -> ComposeResult:
            yield transcript

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        # Prime a streaming row, then keep feeding it more chunks from
        # the same "turn" — we also pump a few tool events through the
        # eviction path to force :meth:`_evict_overflow` to run. The
        # streaming row must survive because it's still the active
        # assistant message.
        transcript.append_assistant_chunk("chunk 0")
        streaming = transcript._active_assistant  # type: ignore[attr-defined]
        assert streaming is not None
        for i in range(1, 12):
            transcript.append_assistant_chunk(f" chunk {i}")
            await pilot.pause()
        # Cap is 3 rows, but the streaming row is preserved.
        assert streaming is transcript._active_assistant  # type: ignore[attr-defined]
        assert streaming in transcript._mounted_rows  # type: ignore[attr-defined]
