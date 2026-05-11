"""Regression test for FallbackEvent handling in ``_run_model_turn``.

When ``call_model`` falls back mid-stream from streaming to non-streaming,
the synthetic events that follow represent the COMPLETE response — not a
continuation. The consumer (``_run_model_turn``) must reset any partial
``turn`` state it accumulated from the aborted partial stream, otherwise
the synthetic events get concatenated on top of partial text/thinking/
tool_uses and the final output is duplicated.

Mirrors the consumer-side half of the critic-flagged regression — the
producer-side half lives in ``test_non_streaming_fallback.py``'s
``test_partial_stream_then_fallback_does_not_double_count_usage``.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace

from src.query.streaming import (
    QueryTurn,
    StreamingQueryState,
    _run_model_turn,
)
from src.query.config import QueryConfig
from src.services.api.claude import (
    ContentBlockStop,
    FallbackEvent,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextDelta,
    UsageEvent,
)
from src.services.api.logging import NonNullableUsage


class _FakeCallModel:
    """An async iterator that produces a curated stream of API events.

    Used by patching ``call_model`` (or driving _run_model_turn with a fake
    client that yields these events from .messages.create stream=True). The
    simplest path here is to monkey-patch ``call_model`` itself via the
    module import.
    """

    def __init__(self, events: list) -> None:
        self._events = list(events)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev


class TestFallbackResetInRunModelTurn(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_event_resets_turn_state(self) -> None:
        """When call_model yields FallbackEvent, the consumer's partial
        accumulation MUST be discarded — the events that follow represent
        the full replay, not a continuation.
        """

        events = [
            MessageStart(model="claude-haiku-4-5", usage=NonNullableUsage(input_tokens=10)),
            # Partial text the stream emitted before failing
            TextDelta(text="partial answer that won't ", index=0),
            # Watchdog fired / stream errored — call_model replays the full
            # response after this marker. The consumer MUST reset
            # turn.text_content here.
            FallbackEvent(cause="watchdog"),
            # The replay — note this is the FULL text, NOT a continuation
            MessageStart(model="claude-haiku-4-5", usage=NonNullableUsage(input_tokens=10)),
            TextDelta(text="the complete final answer.", index=0),
            ContentBlockStop(index=0),
            MessageDelta(stop_reason="end_turn", usage=NonNullableUsage(output_tokens=20)),
            MessageStop(),
            UsageEvent(usage=NonNullableUsage(input_tokens=10, output_tokens=20)),
        ]

        # Patch call_model in the streaming module so _run_model_turn sees
        # our curated events.
        import src.query.streaming as streaming_mod

        original_call_model = streaming_mod.call_model

        def _fake_call_model(messages, options, client):
            return _FakeCallModel(events)

        streaming_mod.call_model = _fake_call_model
        try:
            config = QueryConfig(max_turns=1)
            state = StreamingQueryState(
                messages=[],
                system_prompt="test",
                tools=[],
                context=MagicMock(),
                config=config,
            )
            turn = QueryTurn(turn_number=1)

            yielded = []
            async for ev in _run_model_turn(state, turn, client=None):
                yielded.append(ev)
        finally:
            streaming_mod.call_model = original_call_model

        # The final text in turn should be ONLY the replay — not
        # "partial answer that won't the complete final answer."
        self.assertEqual(turn.text_content, "the complete final answer.")

        # The fallback event should have been surfaced to the caller so
        # the UI can hide partial-stream chrome.
        fallback_qevents = [e for e in yielded if e.type == "fallback"]
        self.assertEqual(len(fallback_qevents), 1)
        self.assertEqual(fallback_qevents[0].data["cause"], "watchdog")


if __name__ == "__main__":
    unittest.main()
