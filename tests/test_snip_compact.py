"""
Tests for Layer 2: Snip Compact — stub (matches TS snipCompact.ts).

TS snipCompact is a stub that returns null. Our Python version matches,
always returning (messages, 0). These tests verify the no-op behavior.
"""

from __future__ import annotations

import unittest

from src.types.content_blocks import ToolResultBlock, ToolUseBlock
from src.types.messages import UserMessage, AssistantMessage
from src.services.compact.snip_compact import snip_compact, SNIPPED_MARKER


def _make_assistant(tool_id: str, tool_name: str = "Read") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input={})],
    )


def _make_user_result(tool_id: str, content: str = "file content here") -> UserMessage:
    return UserMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
    )


class TestSnipCompact(unittest.TestCase):
    """Tests for snip_compact() — stub, always no-op."""

    def test_empty_messages(self):
        result, saved = snip_compact([])
        self.assertEqual(result, [])
        self.assertEqual(saved, 0)

    def test_no_op_returns_zero_saved(self):
        messages = [
            _make_assistant("t1"),
            _make_user_result("t1", "old " * 200),
            _make_assistant("t2"),
            _make_user_result("t2", "also old " * 200),
            _make_assistant("t3"),
            _make_user_result("t3", "recent result"),
        ]
        result, saved = snip_compact(messages, keep_recent=1)
        self.assertEqual(saved, 0)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[1].content[0].content, "old " * 200)
        self.assertEqual(result[3].content[0].content, "also old " * 200)
        self.assertEqual(result[5].content[0].content, "recent result")

    def test_preserves_all_messages(self):
        messages = [
            _make_assistant("t1"),
            _make_user_result("t1", "content " * 500),
            _make_assistant("t2"),
            _make_user_result("t2", "recent"),
        ]
        result, saved = snip_compact(messages, keep_recent=1)
        self.assertEqual(saved, 0)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[1].content[0].content, "content " * 500)


if __name__ == "__main__":
    unittest.main()
