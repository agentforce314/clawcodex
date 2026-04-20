"""
Tests for the compact service.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.conversation import Conversation
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message
from src.providers.base import ChatResponse


class MockConversation:
    """Mock conversation for testing (simple list-based)."""

    def __init__(self, messages=None):
        self.messages = messages or []

    def get_messages(self):
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self.messages
        ]

    def clear(self):
        self.messages.clear()


class TestCompactBoundaryMessages(unittest.TestCase):
    """Tests for compact boundary message creation."""

    def test_creates_boundary_message(self):
        """Boundary message is created with correct properties."""
        from src.compact_service.messages import create_compact_boundary_message, is_compact_boundary_message
        msg = create_compact_boundary_message(
            trigger="manual",
            pre_compact_token_count=5000,
            messages_summarized=10,
        )
        self.assertTrue(is_compact_boundary_message(msg))
        self.assertEqual(msg.role, "system")
        self.assertTrue(getattr(msg, "isMeta", False))

    def test_non_boundary_is_not_boundary(self):
        """Regular messages are not boundary messages."""
        from src.compact_service.messages import is_compact_boundary_message
        msg = Message(role="user", content="Hello")
        self.assertFalse(is_compact_boundary_message(msg))

    def test_get_messages_after_boundary_with_no_boundary(self):
        """Returns all messages when no boundary exists."""
        from src.compact_service.messages import get_messages_after_boundary
        messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi"),
        ]
        result = get_messages_after_boundary(messages)
        self.assertEqual(len(result), 2)

    def test_get_messages_after_boundary_with_boundary(self):
        """Returns only messages after last boundary."""
        from src.compact_service.messages import create_compact_boundary_message, get_messages_after_boundary
        messages = [
            Message(role="user", content="Old"),
            Message(role="assistant", content="Old response"),
        ]
        boundary = create_compact_boundary_message(trigger="manual")
        messages.append(boundary)
        messages.append(Message(role="user", content="New"))
        messages.append(Message(role="assistant", content="New response"))

        result = get_messages_after_boundary(messages)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].content, "New")
        self.assertEqual(result[1].content, "New response")

    def test_summary_message_format(self):
        """Summary message has correct role and content."""
        from src.compact_service.messages import create_compact_summary_message
        msg = create_compact_summary_message(
            "The user was working on a Python project. "
            "They asked about implementing a feature."
        )
        self.assertEqual(msg.role, "user")
        # msg.content is a list of ContentBlock, not a single TextContentBlock
        self.assertIn("continued from a previous conversation", msg.content[0].text)


class TestCompactConversation(unittest.TestCase):
    """Tests for compact_conversation()."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        """Clean up test fixtures."""
        self.tmpdir.cleanup()

    def _make_conversation(self, messages):
        """Create a Conversation with given messages."""
        conv = Conversation()
        conv.messages = messages
        return conv

    def test_not_enough_messages_raises(self):
        """Less than 2 messages raises ValueError."""
        from src.compact_service.service import compact_conversation
        conv = self._make_conversation([
            Message(role="user", content="Hello"),
        ])

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(
            return_value=ChatResponse(content="Summary", model="test", usage={}, finish_reason="stop")
        )

        with self.assertRaises(ValueError) as ctx:
            asyncio.run(compact_conversation(conv, mock_provider, "claude-sonnet-4-6"))
        self.assertIn("Not enough messages", str(ctx.exception))

    def test_sync_fallback_on_llm_failure(self):
        """Falls back to sync extraction on LLM failure."""
        from src.compact_service.service import compact_conversation
        conv = self._make_conversation([
            Message(role="user", content="Hello world " * 50),
            Message(role="assistant", content="Hi there! " * 50),
            Message(role="user", content="What about the code? " * 50),
        ])

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(side_effect=Exception("LLM failed"))
        mock_provider.chat = MagicMock(side_effect=Exception("Sync LLM failed"))

        result = asyncio.run(compact_conversation(conv, mock_provider, "claude-sonnet-4-6"))
        self.assertEqual(result.trigger, "manual")
        self.assertIn("Conversation had", result.summary_text)

    def test_boundary_and_summary_inserted(self):
        """Boundary and summary messages are inserted into conversation."""
        from src.compact_service.service import compact_conversation
        conv = self._make_conversation([
            Message(role="user", content="Hello world " * 50),
            Message(role="assistant", content="Hi there! " * 50),
            Message(role="user", content="What about the code? " * 50),
            Message(role="assistant", content="Here's the code... " * 50),
        ])
        original_count = len(conv.messages)

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(
            return_value=ChatResponse(
                content="User worked on Python code. Assistant helped with implementation.",
                model="test",
                usage={},
                finish_reason="stop"
            )
        )

        result = asyncio.run(compact_conversation(conv, mock_provider, "claude-sonnet-4-6"))

        # Boundary and summary are added (2 new messages)
        self.assertEqual(len(conv.messages), result.post_compact_count)
        self.assertLess(len(conv.messages), original_count)

        # Check that internal boundary is present
        boundary = next(
            (m for m in conv.messages if getattr(m, "isMeta", False)),
            None
        )
        self.assertIsNotNone(boundary)

    def test_custom_instructions_passed_to_llm(self):
        """Custom instructions are included in the prompt."""
        from src.compact_service.service import compact_conversation
        conv = self._make_conversation([
            Message(role="user", content="Hello " * 20),
            Message(role="assistant", content="Hi " * 20),
        ])

        captured_messages = []

        def capture_messages(*args, **kwargs):
            captured_messages.append(kwargs.get("messages", args[1] if len(args) > 1 else []))
            return ChatResponse(content="Summary", model="test", usage={}, finish_reason="stop")

        mock_provider = MagicMock()
        mock_provider.chat_async = AsyncMock(side_effect=capture_messages)

        asyncio.run(compact_conversation(
            conv, mock_provider, "claude-sonnet-4-6",
            custom_instructions="Focus on the Python code"
        ))

        # Custom instruction should appear in the last message
        self.assertGreater(len(captured_messages), 0)
        last_msg = captured_messages[0][-1] if captured_messages else {}
        self.assertIn("Focus on the Python code", last_msg.get("content", ""))


class TestCompactIntegration(unittest.TestCase):
    """Integration tests for compact with mock REPL flow."""

    def test_compact_preserves_boundary_marker_in_serialization(self):
        """Boundary markers are preserved when conversation is serialized."""
        from src.compact_service.messages import create_compact_boundary_message
        conv = Conversation()
        conv.messages.append(Message(role="user", content="Hello"))
        conv.messages.append(Message(role="assistant", content="Hi"))
        boundary = create_compact_boundary_message(trigger="manual", pre_compact_token_count=1000)
        conv.messages.append(boundary)

        # Serialize
        data = conv.to_dict()
        self.assertEqual(len(data["messages"]), 3)

        # Boundary should have isMeta=True in serialized form
        boundary_data = data["messages"][2]
        self.assertTrue(boundary_data.get("isMeta", False))

        # Deserialize
        restored = Conversation.from_dict(data)
        self.assertEqual(len(restored.messages), 3)

        # Boundary should still be internal
        self.assertTrue(getattr(restored.messages[2], "isMeta", False))

        # get_messages() should skip internal messages
        api_messages = restored.get_messages()
        self.assertEqual(len(api_messages), 2)  # Only user + assistant

    def test_compact_result_dataclass(self):
        """CompactionResult dataclass has expected fields."""
        from src.command_system.types import CompactionResult
        result = CompactionResult(
            pre_compact_count=10,
            post_compact_count=2,
            tokens_saved=5000,
            trigger="manual",
            summary_preview="User worked on...",
        )
        self.assertEqual(result.pre_compact_count, 10)
        self.assertEqual(result.post_compact_count, 2)
        self.assertEqual(result.tokens_saved, 5000)
        self.assertEqual(result.trigger, "manual")
        self.assertIn("User worked on", result.summary_preview)


class TestPreservedSegment(unittest.TestCase):
    """Tests for PreservedSegment and annotate_boundary_with_preserved_segment."""

    def test_preserved_segment_dataclass(self):
        from src.compact_service.messages import PreservedSegment
        ps = PreservedSegment(
            head_uuid="head-1234",
            anchor_uuid="anchor-5678",
            tail_uuid="tail-9abc",
        )
        self.assertEqual(ps.head_uuid, "head-1234")
        self.assertEqual(ps.anchor_uuid, "anchor-5678")
        self.assertEqual(ps.tail_uuid, "tail-9abc")

    def test_metadata_includes_preserved_segment(self):
        from src.compact_service.messages import (
            CompactBoundaryMetadata,
            PreservedSegment,
            _serialize_metadata,
        )
        meta = CompactBoundaryMetadata(
            trigger="auto",
            pre_compact_token_count=5000,
            preserved_segment=PreservedSegment(
                head_uuid="h1234567-rest",
                anchor_uuid="a1234567-rest",
                tail_uuid="t1234567-rest",
            ),
        )
        serialized = _serialize_metadata(meta)
        self.assertIn("preserved=h1234567..t1234567", serialized)

    def test_annotate_boundary_no_keep(self):
        from src.compact_service.messages import (
            create_compact_boundary_message,
            annotate_boundary_with_preserved_segment,
        )
        boundary = create_compact_boundary_message(trigger="auto")
        result = annotate_boundary_with_preserved_segment(
            boundary, "anchor-uuid", []
        )
        self.assertIs(result, boundary)

    def test_annotate_boundary_with_keep(self):
        from src.compact_service.messages import (
            create_compact_boundary_message,
            annotate_boundary_with_preserved_segment,
        )
        boundary = create_compact_boundary_message(trigger="auto")
        msg1 = Message(role="user", content="hello")
        msg1.uuid = "uuid-head"
        msg2 = Message(role="assistant", content="hi")
        msg2.uuid = "uuid-tail"
        result = annotate_boundary_with_preserved_segment(
            boundary, "uuid-anchor", [msg1, msg2]
        )
        meta = getattr(result, "_compact_boundary_meta", None)
        self.assertIsNotNone(meta)
        self.assertIsNotNone(meta.preserved_segment)
        self.assertEqual(meta.preserved_segment.head_uuid, "uuid-head")
        self.assertEqual(meta.preserved_segment.anchor_uuid, "uuid-anchor")
        self.assertEqual(meta.preserved_segment.tail_uuid, "uuid-tail")


class TestParsePromptTooLongTokenGap(unittest.TestCase):
    """Tests for parse_prompt_too_long_token_gap (port of TS regex)."""

    def test_standard_anthropic_message(self):
        from src.services.compact.compact import parse_prompt_too_long_token_gap
        gap = parse_prompt_too_long_token_gap(
            "prompt is too long: 137500 tokens > 135000 maximum"
        )
        self.assertEqual(gap, 2500)

    def test_case_insensitive(self):
        from src.services.compact.compact import parse_prompt_too_long_token_gap
        gap = parse_prompt_too_long_token_gap("PROMPT IS TOO LONG: 200 tokens > 100")
        self.assertEqual(gap, 100)

    def test_singular_token_unit(self):
        from src.services.compact.compact import parse_prompt_too_long_token_gap
        gap = parse_prompt_too_long_token_gap("prompt is too long: 101 token > 100")
        self.assertEqual(gap, 1)

    def test_unparseable_returns_none(self):
        from src.services.compact.compact import parse_prompt_too_long_token_gap
        self.assertIsNone(parse_prompt_too_long_token_gap("rate limit exceeded"))
        self.assertIsNone(parse_prompt_too_long_token_gap(""))

    def test_zero_or_negative_gap_returns_none(self):
        from src.services.compact.compact import parse_prompt_too_long_token_gap
        self.assertIsNone(
            parse_prompt_too_long_token_gap("prompt is too long: 100 tokens > 100")
        )
        self.assertIsNone(
            parse_prompt_too_long_token_gap("prompt is too long: 50 tokens > 100")
        )


class TestCollectDiscoveredToolNames(unittest.TestCase):
    """Tests for the discovered-tools collector used by boundary metadata."""

    def test_collects_unique_sorted_names(self):
        from src.services.compact.compact import _collect_discovered_tool_names
        from src.types.messages import AssistantMessage
        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={}),
                ToolUseBlock(id="t2", name="Write", input={}),
            ]),
            AssistantMessage(content=[
                ToolUseBlock(id="t3", name="Read", input={}),
                ToolUseBlock(id="t4", name="Bash", input={}),
            ]),
        ]
        self.assertEqual(
            _collect_discovered_tool_names(msgs),
            ["Bash", "Read", "Write"],
        )

    def test_skips_user_messages(self):
        from src.services.compact.compact import _collect_discovered_tool_names
        msgs = [Message(role="user", content="hello")]
        self.assertEqual(_collect_discovered_tool_names(msgs), [])

    def test_handles_dict_blocks(self):
        from src.services.compact.compact import _collect_discovered_tool_names
        from src.types.messages import AssistantMessage
        msgs = [
            AssistantMessage(content=[
                {"type": "tool_use", "id": "t1", "name": "Glob", "input": {}},
            ]),
        ]
        self.assertEqual(_collect_discovered_tool_names(msgs), ["Glob"])

    def test_empty_messages(self):
        from src.services.compact.compact import _collect_discovered_tool_names
        self.assertEqual(_collect_discovered_tool_names([]), [])


class TestCompactServiceForwardsAttachmentContext(unittest.TestCase):
    """Tests that compact_service.compact_conversation forwards attachment context."""

    def test_forwards_read_file_state_to_pipeline(self):
        from src.compact_service.service import compact_conversation
        import tempfile, os, time

        # Create a real file so the attachment is actually generated
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("print('x')")
            f.flush()
            tmp_path = f.name

        try:
            conv = Conversation()
            conv.messages = [
                Message(role="user", content="hi " * 50),
                Message(role="assistant", content="hello " * 50),
            ]

            mock_provider = MagicMock()
            mock_provider.chat_async = AsyncMock(
                return_value=ChatResponse(
                    content="summary",
                    model="test",
                    usage={},
                    finish_reason="stop",
                )
            )

            result = asyncio.run(compact_conversation(
                conv, mock_provider, "claude-sonnet-4-6",
                read_file_state={
                    tmp_path: {"content": "print('x')", "timestamp": time.time()}
                },
            ))

            # The attachment should be present in the conversation after compact
            attachment_present = any(
                getattr(m, "isMeta", False) and isinstance(m.content, str)
                and tmp_path in m.content
                for m in conv.messages
            )
            self.assertTrue(
                attachment_present,
                "Post-compact file attachment should be inserted into conversation",
            )
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
