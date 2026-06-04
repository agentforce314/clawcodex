"""Tests for Phase-12 assistant-thinking widget (gap #16 sub-item)."""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from src.tui.widgets.messages.assistant_thinking import AssistantThinkingMessage


class _Harness(App):
    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_streaming_chunks_accumulate() -> None:
    row = AssistantThinkingMessage()
    async with _Harness(row).run_test() as pilot:
        row.append_chunk("Let me think")
        row.append_chunk(" about this.")
        await pilot.pause()
        assert row.streaming_text == "Let me think about this."


@pytest.mark.asyncio
async def test_post_finalise_chunks_are_discarded() -> None:
    row = AssistantThinkingMessage()
    async with _Harness(row).run_test() as pilot:
        row.append_chunk("preliminary thought")
        row.finalise("final thought")
        row.append_chunk(" extra (should be ignored)")
        await pilot.pause()
        assert row._final_text == "final thought"


@pytest.mark.asyncio
async def test_redacted_variant_marks_class() -> None:
    row = AssistantThinkingMessage(redacted=True)
    async with _Harness(row).run_test() as pilot:
        await pilot.pause()
        assert row.has_class("-redacted")


@pytest.mark.asyncio
async def test_snapshot_returns_renderable_when_present() -> None:
    row = AssistantThinkingMessage()
    async with _Harness(row).run_test() as pilot:
        row.append_chunk("considered options")
        row.finalise("considered options")
        await pilot.pause()
        snap = row.snapshot()
        assert snap is not None


@pytest.mark.asyncio
async def test_snapshot_returns_none_when_empty() -> None:
    row = AssistantThinkingMessage()
    async with _Harness(row).run_test() as pilot:
        await pilot.pause()
        assert row.snapshot() is None


@pytest.mark.asyncio
async def test_finalise_with_empty_text_uses_stream() -> None:
    """If finalise is called without text, the streamed content stands in."""

    row = AssistantThinkingMessage()
    async with _Harness(row).run_test() as pilot:
        row.append_chunk("partial only")
        row.finalise("")
        await pilot.pause()
        assert row._final_text == "partial only"
