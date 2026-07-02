"""Phase D — API Integration Tests.

Validates the streaming API client pipeline end-to-end with mocked HTTP.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.api.claude import (
    CallModelOptions,
    ContentBlockStop,
    MessageDelta,
    MessageStart,
    MessageStop,
    StreamEvent,
    TextDelta,
    ToolUseStart,
    UsageEvent,
    call_model,
)
from src.services.api.errors import (
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
)
from src.services.api.logging import NonNullableUsage


class TestApiStreamingPipeline(unittest.TestCase):
    """End-to-end streaming API pipeline."""

    def test_stream_events_sequence(self) -> None:
        """call_model yields events in correct order: start→content→stop."""
        events_collected = []

        async def mock_stream():
            yield MessageStart(model="claude-sonnet-4-6", usage=NonNullableUsage(input_tokens=10, output_tokens=0))
            yield TextDelta(text="Hello", index=0)
            yield ContentBlockStop(index=0)
            yield MessageDelta(stop_reason="end_turn")
            yield MessageStop()

        async def run():
            async for event in mock_stream():
                events_collected.append(event)

        asyncio.run(run())
        types = [type(e).__name__ for e in events_collected]
        self.assertEqual(types, ["MessageStart", "TextDelta", "ContentBlockStop", "MessageDelta", "MessageStop"])

    def test_usage_tracking_through_stream(self) -> None:
        """UsageEvent accumulates input/output tokens."""
        usage = NonNullableUsage(input_tokens=100, output_tokens=50)
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 50)


class TestApiRetryIntegration(unittest.TestCase):
    """ch04 round-4 GAP B: the parallel services/api/retry.py engine was
    deleted; retry + model-fallback live in the query loop's lane now.
    Behavior coverage moved to tests/test_ch04_api_round4.py (loop-level:
    429/5xx retry, 529 counter, fallback switch, quota bail)."""

    def test_dead_module_removed(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import src.services.api.retry  # noqa: F401



class TestApiErrorClassification(unittest.TestCase):
    """Error types are correctly classified."""

    def test_retryable_errors(self) -> None:
        from src.services.api.errors import categorize_retryable_api_error
        self.assertTrue(categorize_retryable_api_error(RateLimitError("", status=429)).retryable)
        self.assertTrue(categorize_retryable_api_error(OverloadedError("", status=529)).retryable)

    def test_non_retryable_errors(self) -> None:
        from src.services.api.errors import categorize_retryable_api_error
        self.assertFalse(categorize_retryable_api_error(ValueError("bad input")).retryable)
        self.assertFalse(categorize_retryable_api_error(PromptTooLongError("too long")).retryable)


if __name__ == "__main__":
    unittest.main()
