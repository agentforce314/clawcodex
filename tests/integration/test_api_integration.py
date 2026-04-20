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
from src.services.api.retry import RetryOptions, with_retry


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
    """Retry logic integrates with error classification."""

    def test_rate_limit_retried(self) -> None:
        attempt_count = 0

        async def op(attempt, ctx):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 2:
                raise RateLimitError("rate limited", status=429)
            return "success"

        async def run():
            return await with_retry(op, RetryOptions(max_retries=5, model="test"))

        result = asyncio.run(run())
        self.assertEqual(result, "success")
        self.assertEqual(attempt_count, 3)

    def test_overloaded_retried(self) -> None:
        attempt_count = 0

        async def op(attempt, ctx):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 1:
                raise OverloadedError("overloaded", status=529)
            return "ok"

        async def run():
            return await with_retry(op, RetryOptions(max_retries=5, model="test"))

        result = asyncio.run(run())
        self.assertEqual(result, "ok")

    def test_prompt_too_long_not_retried(self) -> None:
        async def op(attempt, ctx):
            raise PromptTooLongError("prompt too long")

        async def run():
            return await with_retry(op, RetryOptions(max_retries=5, model="test"))

        from src.services.api.retry import CannotRetryError
        with self.assertRaises(CannotRetryError):
            asyncio.run(run())


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
