"""
Tests for Layer 5: Autocompact.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.providers.base import ChatResponse

from src.services.compact.autocompact import (
    AutoCompactTracking,
    should_auto_compact,
    auto_compact_if_needed,
    get_effective_context_window_size,
    get_auto_compact_threshold,
    is_auto_compact_enabled,
    calculate_token_warning_state,
    MIN_INPUT_TOKENS_FOR_AUTOCOMPACT,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    AUTOCOMPACT_BUFFER_TOKENS,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
)


class TestGetEffectiveContextWindowSize(unittest.TestCase):
    """Tests for get_effective_context_window_size()."""

    def test_basic_calculation(self):
        """Subtracts reserved tokens from context window."""
        effective = get_effective_context_window_size(200_000)
        self.assertEqual(effective, 200_000 - MAX_OUTPUT_TOKENS_FOR_SUMMARY)

    def test_with_custom_max_output(self):
        """Uses min(max_output, 20_000) as reserve."""
        effective = get_effective_context_window_size(200_000, max_output_tokens=10_000)
        self.assertEqual(effective, 200_000 - 10_000)

    def test_with_large_max_output(self):
        """Caps reserve at MAX_OUTPUT_TOKENS_FOR_SUMMARY."""
        effective = get_effective_context_window_size(200_000, max_output_tokens=50_000)
        self.assertEqual(effective, 200_000 - MAX_OUTPUT_TOKENS_FOR_SUMMARY)

    def test_floor(self):
        """Effective context has a floor to prevent negative thresholds."""
        effective = get_effective_context_window_size(25_000)
        self.assertGreaterEqual(effective, MAX_OUTPUT_TOKENS_FOR_SUMMARY + AUTOCOMPACT_BUFFER_TOKENS)

    @patch.dict(os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"})
    def test_env_override(self):
        """CLAUDE_CODE_AUTO_COMPACT_WINDOW caps the context window."""
        effective = get_effective_context_window_size(200_000)
        self.assertEqual(effective, 100_000 - MAX_OUTPUT_TOKENS_FOR_SUMMARY)


class TestGetAutoCompactThreshold(unittest.TestCase):
    """Tests for get_auto_compact_threshold()."""

    def test_basic_threshold(self):
        """Threshold = effective context window - buffer."""
        threshold = get_auto_compact_threshold(200_000)
        effective = get_effective_context_window_size(200_000)
        self.assertEqual(threshold, effective - AUTOCOMPACT_BUFFER_TOKENS)

    @patch.dict(os.environ, {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "50"})
    def test_percentage_override(self):
        """Percentage override works."""
        threshold = get_auto_compact_threshold(200_000)
        effective = get_effective_context_window_size(200_000)
        expected = int(effective * 0.5)
        self.assertEqual(threshold, expected)


class TestIsAutoCompactEnabled(unittest.TestCase):
    """Tests for is_auto_compact_enabled()."""

    def test_enabled_by_default(self):
        self.assertTrue(is_auto_compact_enabled())

    @patch.dict(os.environ, {"DISABLE_COMPACT": "1"})
    def test_disabled_by_disable_compact(self):
        self.assertFalse(is_auto_compact_enabled())

    @patch.dict(os.environ, {"DISABLE_AUTO_COMPACT": "true"})
    def test_disabled_by_disable_auto_compact(self):
        self.assertFalse(is_auto_compact_enabled())


class TestCalculateTokenWarningState(unittest.TestCase):
    """Tests for calculate_token_warning_state()."""

    def test_low_usage(self):
        state = calculate_token_warning_state(10_000, 200_000)
        self.assertFalse(state["is_above_warning_threshold"])
        self.assertFalse(state["is_above_error_threshold"])
        self.assertFalse(state["is_above_auto_compact_threshold"])
        self.assertFalse(state["is_at_blocking_limit"])
        self.assertGreater(state["percent_left"], 50)

    def test_high_usage(self):
        threshold = get_auto_compact_threshold(200_000)
        state = calculate_token_warning_state(threshold + 1, 200_000)
        self.assertTrue(state["is_above_auto_compact_threshold"])


class TestShouldAutoCompact(unittest.TestCase):
    """Tests for should_auto_compact()."""

    def test_below_minimum_tokens(self):
        """Does not trigger when input tokens are below minimum."""
        self.assertFalse(should_auto_compact(5_000, 200_000))

    def test_below_threshold(self):
        """Does not trigger when below threshold."""
        self.assertFalse(should_auto_compact(100_000, 200_000))

    def test_above_threshold_triggers(self):
        """Triggers when input tokens exceed threshold."""
        threshold = get_auto_compact_threshold(200_000)
        self.assertTrue(should_auto_compact(threshold + 1, 200_000))

    def test_exact_threshold(self):
        """Triggers at exactly the threshold."""
        threshold = get_auto_compact_threshold(200_000)
        self.assertTrue(should_auto_compact(threshold, 200_000))

    def test_circuit_breaker_blocks(self):
        """Circuit breaker blocks after consecutive failures."""
        tracking = AutoCompactTracking(
            consecutive_failures=MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
        )
        threshold = get_auto_compact_threshold(200_000)
        self.assertFalse(should_auto_compact(threshold + 1, 200_000, tracking=tracking))

    @patch.dict(os.environ, {"DISABLE_AUTO_COMPACT": "1"})
    def test_disabled(self):
        """Does not trigger when disabled."""
        threshold = get_auto_compact_threshold(200_000)
        self.assertFalse(should_auto_compact(threshold + 1, 200_000))


class TestAutoCompactIfNeeded(unittest.TestCase):
    """Tests for auto_compact_if_needed()."""

    def _make_messages(self, count: int = 5) -> list[Message]:
        messages = []
        for i in range(count):
            messages.append(UserMessage(content=f"User message {i}"))
            messages.append(AssistantMessage(
                content=[TextBlock(text=f"Assistant response {i}")],
            ))
        return messages

    def test_no_compact_below_threshold(self):
        """Returns None when below threshold."""
        provider = MagicMock()
        result = asyncio.run(auto_compact_if_needed(
            self._make_messages(),
            input_token_count=5_000,
            context_window=200_000,
            provider=provider,
            model="test-model",
        ))
        self.assertIsNone(result)
        provider.chat_async.assert_not_called()

    def test_compact_above_threshold(self):
        """Returns CompactionResult when above threshold."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary of conversation",
            model="test",
            usage={"input_tokens": 100, "output_tokens": 50},
            finish_reason="stop",
        ))
        threshold = get_auto_compact_threshold(200_000)
        result = asyncio.run(auto_compact_if_needed(
            self._make_messages(),
            input_token_count=threshold + 1,
            context_window=200_000,
            provider=provider,
            model="test-model",
        ))
        self.assertIsNotNone(result)
        self.assertEqual(result.trigger, "auto")

    def test_tracks_success(self):
        """Successful compaction resets failures and increments count."""
        tracking = AutoCompactTracking(consecutive_failures=2)
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary",
            model="test",
            usage={},
            finish_reason="stop",
        ))
        threshold = get_auto_compact_threshold(200_000)
        result = asyncio.run(auto_compact_if_needed(
            self._make_messages(),
            input_token_count=threshold + 1,
            context_window=200_000,
            provider=provider,
            model="test-model",
            tracking=tracking,
        ))
        self.assertIsNotNone(result)
        self.assertEqual(tracking.consecutive_failures, 0)
        self.assertEqual(tracking.total_compactions, 1)

    @patch.dict(os.environ, {"DISABLE_COMPACT": "1"})
    def test_disabled_returns_none(self):
        """Returns None when compact is disabled."""
        provider = MagicMock()
        threshold = get_auto_compact_threshold(200_000)
        result = asyncio.run(auto_compact_if_needed(
            self._make_messages(),
            input_token_count=threshold + 1,
            context_window=200_000,
            provider=provider,
            model="test-model",
        ))
        self.assertIsNone(result)

    def test_forwards_attachment_context(self):
        """auto_compact_if_needed forwards read_file_state to the pipeline."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("print('x')")
            f.flush()
            tmp_path = f.name

        try:
            provider = MagicMock()
            provider.chat_async = AsyncMock(return_value=ChatResponse(
                content="Summary",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 50},
                finish_reason="stop",
            ))

            threshold = get_auto_compact_threshold(200_000)
            result = asyncio.run(auto_compact_if_needed(
                self._make_messages(),
                input_token_count=threshold + 100,
                context_window=200_000,
                provider=provider,
                model="test-model",
                read_file_state={
                    tmp_path: {"content": "print('x')", "timestamp": time.time()}
                },
            ))
            self.assertIsNotNone(result)
            self.assertGreaterEqual(len(result.attachments), 1)
            # Attachment should reference the file we passed in.
            self.assertTrue(any(
                tmp_path in m.content for m in result.attachments
                if isinstance(m.content, str)
            ))
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
