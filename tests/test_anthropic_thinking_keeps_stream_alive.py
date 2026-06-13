"""Regression test for the thinking-aware streaming liveness fix.

Before this fix, ``chat_stream_response`` iterated ``stream.text_stream``
which only yields on ``text_delta`` events. When extended thinking is
enabled (Claude 4.x), the model often emits 60-120s+ of ``thinking_delta``
events with no text in between while it works through a hard prompt.
The watchdog (default 90s idle) would interpret that as a hung stream,
close it, and fall back to non-streaming ``chat()`` — which the Anthropic
SDK rejects with "Streaming is required for operations that may take
longer than 10 minutes." So every long-prompt + thinking request looked
empty downstream.

This module pins the fixed behavior: the watchdog now resets on every
event from the full stream (including thinking deltas), and only
``text_delta`` events contribute to the visible output.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from src.providers.anthropic_provider import AnthropicProvider


class _MockEvent:
    """Minimal stand-in for an Anthropic streaming event with a delta.

    The provider reads only ``getattr(event, "delta", None)`` and
    ``getattr(delta, "text", None)``, so we don't need the full SDK
    types — just the duck-typed shape.
    """

    def __init__(self, delta=None):
        self.delta = delta


class _TextDelta:
    type = "text_delta"

    def __init__(self, text: str):
        self.text = text


class _ThinkingDelta:
    type = "thinking_delta"

    def __init__(self, thinking: str):
        self.thinking = thinking
        # No ``text`` attribute — that's the point.


def _build_provider_with_fake_stream(events: list) -> tuple[AnthropicProvider, MagicMock, MagicMock]:
    """Wire a provider whose ``client.messages.stream`` yields ``events``."""

    fake_response = MagicMock()
    fake_response.close = MagicMock()

    fake_stream = MagicMock()
    fake_stream.__iter__ = MagicMock(return_value=iter(events))
    fake_stream.response = fake_response

    # Force the post-stream code path to fall back to ``streamed_text``
    # by making ``get_final_message`` raise. ``_build_chat_response`` only
    # runs when ``final_message`` is non-None, so we steer past it for the
    # tests that care about the streamed-text accumulation path.
    fake_stream.get_final_message = MagicMock(
        side_effect=RuntimeError("final message unavailable")
    )

    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=fake_stream)
    stream_cm.__exit__ = MagicMock(return_value=False)

    fake_client = MagicMock()
    fake_client.messages.stream.return_value = stream_cm

    provider = AnthropicProvider(api_key="test", model="claude-opus-4-6")
    provider.client = fake_client
    return provider, fake_stream, fake_response


class TestThinkingDeltasKeepStreamAlive(unittest.TestCase):
    """When thinking events flow without text, the watchdog must not fire."""

    def test_long_thinking_burst_does_not_trigger_fallback(self):
        """Stream emits a burst of thinking events spread across more time
        than the watchdog's idle deadline, then a few text deltas, then
        completes. The watchdog must NOT fire and the wrapper must NOT
        fall back to ``chat()`` — the visible output is the concatenation
        of the text deltas, thinking is never surfaced."""

        captured_text_chunks: list[str] = []

        def slow_thinking_then_text():
            # Five thinking events spaced ~30ms apart — totalling ~150ms,
            # well past the 50ms watchdog deadline we'll configure. With
            # the old text_stream iteration, this would fire the
            # watchdog because no text deltas arrive during this window.
            for chunk in ("step 1", "step 2", "step 3", "step 4", "step 5"):
                time.sleep(0.03)
                yield _MockEvent(delta=_ThinkingDelta(chunk))
            # Then two text deltas — the only events that should populate
            # streamed_text and fire on_text_chunk.
            yield _MockEvent(delta=_TextDelta("hello "))
            yield _MockEvent(delta=_TextDelta("world"))

        provider, fake_stream, fake_response = _build_provider_with_fake_stream(
            list(slow_thinking_then_text())
        )

        with patch.dict("os.environ", {"CLAUDE_STREAM_IDLE_TIMEOUT_MS": "50"}), \
             patch.object(provider, "chat") as mock_chat_fallback:
            result = provider.chat_stream_response(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                on_text_chunk=captured_text_chunks.append,
            )

        # Fallback must NOT have been invoked — the thinking events kept
        # the stream alive past the would-have-fired-under-text_stream
        # watchdog deadline.
        mock_chat_fallback.assert_not_called()
        # Watchdog must NOT have closed the underlying response.
        fake_response.close.assert_not_called()
        # Visible output is the concatenated text-delta payload — thinking
        # text is deliberately excluded.
        self.assertEqual(result.content, "hello world")
        # ``on_text_chunk`` fires once per text delta, not once per event.
        self.assertEqual(captured_text_chunks, ["hello ", "world"])


class TestEventsWithoutDeltasAreSafe(unittest.TestCase):
    """Events with no ``delta`` attribute (message_start / message_stop /
    content_block_start / content_block_stop) reset the watchdog but
    don't populate text. The wrapper must not crash on them."""

    def test_iteration_over_no_delta_events_completes(self):
        events = [
            _MockEvent(delta=None),  # MessageStartEvent shape
            _MockEvent(delta=None),  # ContentBlockStartEvent shape
            _MockEvent(delta=_TextDelta("ok")),
            _MockEvent(delta=None),  # ContentBlockStopEvent shape
            _MockEvent(delta=None),  # MessageStopEvent shape
        ]

        provider, _, _ = _build_provider_with_fake_stream(events)
        captured: list[str] = []
        with patch.object(provider, "chat") as mock_chat_fallback:
            result = provider.chat_stream_response(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                on_text_chunk=captured.append,
            )
        mock_chat_fallback.assert_not_called()
        self.assertEqual(result.content, "ok")
        self.assertEqual(captured, ["ok"])


if __name__ == "__main__":
    unittest.main()
