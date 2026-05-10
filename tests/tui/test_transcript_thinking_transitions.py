"""Tests for the thinking↔assistant active-row transition logic.

Phase-12 close-out (Critic-flagged): the symmetric guard in
``Transcript.append_assistant_chunk`` retires a thinking active row
before mounting a new assistant text row, and vice versa.

The agent-loop dispatch (which would invoke these helpers in
production) is plan-deferred to a follow-up; these tests pin the
behavior so the wiring is mechanical when the time comes.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from src.tui.widgets.messages.assistant_text import AssistantTextMessage
from src.tui.widgets.messages.assistant_thinking import AssistantThinkingMessage
from src.tui.widgets.transcript_view import Transcript


class _Harness(App):
    def __init__(self, transcript: Transcript) -> None:
        super().__init__()
        self._transcript = transcript

    def compose(self) -> ComposeResult:
        yield self._transcript


@pytest.mark.asyncio
async def test_assistant_after_thinking_retires_thinking_row() -> None:
    """Thinking row → assistant row: the thinking row is finalised
    before the assistant row mounts. Symmetric guard test."""

    transcript = Transcript()
    async with _Harness(transcript).run_test() as pilot:
        transcript.append_thinking_chunk("considering options")
        await pilot.pause()
        # Active row is a thinking widget.
        assert isinstance(transcript._active_assistant, AssistantThinkingMessage)

        transcript.append_assistant_chunk("here is the answer")
        await pilot.pause()
        # Assistant chunk arrived: thinking row should have been retired
        # and a fresh AssistantTextMessage mounted.
        assert isinstance(transcript._active_assistant, AssistantTextMessage)
        # The thinking row's content stays in the buffer — finalised, not deleted.
        assert any(
            isinstance(row, AssistantThinkingMessage)
            for row in transcript._mounted_rows
        )


@pytest.mark.asyncio
async def test_thinking_after_assistant_retires_assistant_row() -> None:
    """Assistant row → thinking row: the assistant row is finalised
    before the thinking row mounts. Mirrors the symmetric guard."""

    transcript = Transcript()
    async with _Harness(transcript).run_test() as pilot:
        transcript.append_assistant_chunk("partial response")
        await pilot.pause()
        assert isinstance(transcript._active_assistant, AssistantTextMessage)

        transcript.append_thinking_chunk("revising approach")
        await pilot.pause()
        assert isinstance(transcript._active_assistant, AssistantThinkingMessage)


@pytest.mark.asyncio
async def test_thinking_to_thinking_redacted_remounts() -> None:
    """Switching from non-redacted to redacted thinking → fresh widget."""

    transcript = Transcript()
    async with _Harness(transcript).run_test() as pilot:
        transcript.append_thinking_chunk("public reasoning")
        await pilot.pause()
        first_row = transcript._active_assistant
        assert isinstance(first_row, AssistantThinkingMessage)
        assert not first_row.has_class("-redacted")

        transcript.append_thinking_chunk("hidden", redacted=True)
        await pilot.pause()
        second_row = transcript._active_assistant
        assert isinstance(second_row, AssistantThinkingMessage)
        assert second_row.has_class("-redacted")
        # Two distinct rows in the mounted list — the previous one was
        # retired, the new redacted one mounted.
        assert first_row is not second_row


@pytest.mark.asyncio
async def test_thinking_chunks_accumulate_in_same_widget() -> None:
    """Same-mode chunks stream into the same row (no remount per chunk)."""

    transcript = Transcript()
    async with _Harness(transcript).run_test() as pilot:
        transcript.append_thinking_chunk("step 1")
        transcript.append_thinking_chunk(", step 2")
        transcript.append_thinking_chunk(", step 3")
        await pilot.pause()
        active = transcript._active_assistant
        assert isinstance(active, AssistantThinkingMessage)
        assert active.streaming_text == "step 1, step 2, step 3"


@pytest.mark.asyncio
async def test_finalise_thinking_via_append_thinking() -> None:
    """The non-streaming finalise path closes out the active thinking row."""

    transcript = Transcript()
    async with _Harness(transcript).run_test() as pilot:
        transcript.append_thinking_chunk("draft thought")
        await pilot.pause()
        transcript.append_thinking("draft thought, finalised")
        await pilot.pause()
        # No active row — the previous thinking widget was finalised.
        assert transcript._active_assistant is None
