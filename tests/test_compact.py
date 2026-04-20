"""
Tests for core compaction (compact.py).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.types.content_blocks import TextBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.providers.base import ChatResponse
from src.compact_service.messages import is_compact_boundary_message

from src.services.compact.compact import (
    CompactContext,
    CompactionResult,
    COMPACT_SYSTEM_PROMPT,
    compact_conversation,
    partial_compact_conversation,
    COMPACT_MAX_OUTPUT_TOKENS,
)


def _make_messages(count: int = 6) -> list[Message]:
    messages: list[Message] = []
    for i in range(count):
        messages.append(UserMessage(content=f"User message {i} " * 20))
        messages.append(AssistantMessage(
            content=[TextBlock(text=f"Assistant response {i} " * 20)],
        ))
    return messages


class TestCompactConversation(unittest.TestCase):
    """Tests for compact_conversation()."""

    def test_produces_boundary_and_summary(self):
        """Compaction produces boundary marker and summary message."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary: User asked about Python code.",
            model="test",
            usage={"input_tokens": 500, "output_tokens": 100},
            finish_reason="stop",
        ))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(4),
            trigger="manual",
        )
        result = asyncio.run(compact_conversation(ctx))

        self.assertIsInstance(result, CompactionResult)
        self.assertTrue(is_compact_boundary_message(result.boundary_marker))
        self.assertGreater(len(result.summary_messages), 0)
        self.assertEqual(result.trigger, "manual")

    def test_not_enough_messages_raises(self):
        """Raises ValueError with fewer than 2 messages."""
        provider = MagicMock()
        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=[UserMessage(content="Hello")],
        )
        with self.assertRaises(ValueError) as cm:
            asyncio.run(compact_conversation(ctx))
        self.assertIn("Not enough", str(cm.exception))

    def test_fallback_on_llm_failure(self):
        """Falls back to text extraction on LLM failure."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(side_effect=Exception("LLM error"))
        provider.chat = MagicMock(side_effect=Exception("Sync also failed"))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(3),
        )
        result = asyncio.run(compact_conversation(ctx))
        # Should succeed with fallback summary
        self.assertIsInstance(result, CompactionResult)
        self.assertGreater(len(result.summary_messages), 0)

    def test_custom_instructions_forwarded(self):
        """Custom instructions are included in the LLM prompt."""
        captured = {}

        async def capture_call(**kwargs):
            captured.update(kwargs)
            return ChatResponse(
                content="Summary",
                model="test",
                usage={},
                finish_reason="stop",
            )

        provider = MagicMock()
        provider.chat_async = AsyncMock(side_effect=capture_call)

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(3),
            custom_instructions="Focus on Python details",
        )
        asyncio.run(compact_conversation(ctx))

        # Check that custom_instructions appear in the messages sent to LLM
        msgs = captured.get("messages", [])
        last_content = msgs[-1]["content"] if msgs else ""
        self.assertIn("Focus on Python details", last_content)

    def test_tokens_saved_reported(self):
        """tokens_saved is calculated and reported."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Brief summary.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(5),
        )
        result = asyncio.run(compact_conversation(ctx))
        self.assertIsNotNone(result.pre_compact_token_count)
        self.assertGreater(result.pre_compact_token_count, 0)

    def test_user_display_message(self):
        """user_display_message is populated."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary of the conversation.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(3),
        )
        result = asyncio.run(compact_conversation(ctx))
        self.assertIsNotNone(result.user_display_message)
        self.assertIn("Compacted", result.user_display_message)


class TestPartialCompactConversation(unittest.TestCase):
    """Tests for partial_compact_conversation()."""

    def test_earlier_direction(self):
        """Partial compact with 'earlier' summarizes prefix."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary of earlier messages.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        messages = _make_messages(4)
        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
        )
        result = asyncio.run(partial_compact_conversation(ctx, pivot_index=4, direction="earlier"))
        self.assertIsInstance(result, CompactionResult)
        self.assertEqual(len(result.messages_to_keep), len(messages) - 4)

    def test_later_direction(self):
        """Partial compact with 'later' summarizes suffix."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary of later messages.",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        messages = _make_messages(4)
        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
        )
        result = asyncio.run(partial_compact_conversation(ctx, pivot_index=4, direction="later"))
        self.assertIsInstance(result, CompactionResult)
        self.assertEqual(len(result.messages_to_keep), 4)

    def test_empty_summarize_raises(self):
        """Raises ValueError if nothing to summarize."""
        provider = MagicMock()
        messages = _make_messages(2)
        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
        )
        with self.assertRaises(ValueError):
            asyncio.run(partial_compact_conversation(ctx, pivot_index=0, direction="earlier"))

    def test_preserved_segment_annotated_for_kept_messages(self):
        """Partial compact annotates the boundary with preserved-segment metadata."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary",
            model="test",
            usage={},
            finish_reason="stop",
        ))

        messages = _make_messages(4)
        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
        )
        # 'earlier' summarizes prefix and keeps the suffix → suffix-preserving.
        # Anchor should be the summary message UUID.
        result = asyncio.run(
            partial_compact_conversation(ctx, pivot_index=4, direction="earlier")
        )

        meta = getattr(result.boundary_marker, "_compact_boundary_meta", None)
        self.assertIsNotNone(meta)
        self.assertIsNotNone(meta.preserved_segment)
        # Anchor should be the summary message UUID (suffix-preserving)
        self.assertEqual(
            meta.preserved_segment.anchor_uuid,
            result.summary_messages[0].uuid,
        )
        # head/tail should match the kept messages
        self.assertEqual(
            meta.preserved_segment.head_uuid,
            result.messages_to_keep[0].uuid,
        )
        self.assertEqual(
            meta.preserved_segment.tail_uuid,
            result.messages_to_keep[-1].uuid,
        )

    def test_partial_compact_captures_usage(self):
        """Partial compact records compaction_usage from response."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary",
            model="test",
            usage={"input_tokens": 1234, "output_tokens": 56},
            finish_reason="stop",
        ))

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(4),
        )
        result = asyncio.run(
            partial_compact_conversation(ctx, pivot_index=4, direction="earlier")
        )
        self.assertIsNotNone(result.compaction_usage)
        self.assertEqual(result.compaction_usage["input_tokens"], 1234)


class TestCompactionParityFixes(unittest.TestCase):
    """Tests for Round 5 parity fixes (system prompt, discovered tools)."""

    def test_system_prompt_passed_to_llm(self):
        """The summarization system prompt is forwarded to the provider."""
        captured = {}

        async def capture_call(**kwargs):
            captured.update(kwargs)
            return ChatResponse(
                content="Summary", model="test", usage={}, finish_reason="stop"
            )

        provider = MagicMock()
        provider.chat_async = AsyncMock(side_effect=capture_call)

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=_make_messages(3),
        )
        asyncio.run(compact_conversation(ctx))

        self.assertEqual(captured.get("system"), COMPACT_SYSTEM_PROMPT)

    def test_discovered_tools_recorded_on_boundary(self):
        """tool_use names from summarized messages are stored on the boundary."""
        provider = MagicMock()
        provider.chat_async = AsyncMock(return_value=ChatResponse(
            content="Summary", model="test", usage={}, finish_reason="stop"
        ))

        messages: list[Message] = [
            UserMessage(content="please read a file"),
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/a"}),
            ]),
            UserMessage(content="thanks"),
            AssistantMessage(content=[
                ToolUseBlock(id="t2", name="Glob", input={"pattern": "*"}),
            ]),
        ]

        ctx = CompactContext(
            provider=provider,
            model="test-model",
            messages=messages,
        )
        result = asyncio.run(compact_conversation(ctx))

        meta = getattr(result.boundary_marker, "_compact_boundary_meta", None)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.pre_compact_discovered_tools, ["Glob", "Read"])


if __name__ == "__main__":
    unittest.main()
