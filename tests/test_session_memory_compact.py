"""
Tests for session memory compaction.
"""

from __future__ import annotations

import unittest

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.session_memory_compact import (
    calculate_messages_to_keep_index,
    adjust_index_to_preserve_api_invariants,
    try_session_memory_compaction,
    SessionMemoryCompactConfig,
    has_text_blocks,
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=text)


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)])


def _assistant_tool(tool_id: str) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_id, name="Read", input={})],
    )


def _user_result(tool_id: str) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_id, content="result")],
    )


class TestCalculateMessagesToKeepIndex(unittest.TestCase):
    """Tests for the token-based calculate_messages_to_keep_index()."""

    def test_empty_messages(self):
        self.assertEqual(calculate_messages_to_keep_index([], 0), 0)

    def test_last_summarized_at_start(self):
        """When last_summarized_index=0, messages from index 1 are candidates."""
        msgs = [_user("a"), _assistant("b"), _user("c")]
        idx = calculate_messages_to_keep_index(msgs, 0)
        self.assertGreaterEqual(idx, 0)
        self.assertLess(idx, len(msgs))

    def test_negative_index_means_no_summarized(self):
        """When last_summarized_index=-1, backward expansion from end."""
        msgs = [_user(f"msg {i}") for i in range(10)]
        idx = calculate_messages_to_keep_index(msgs, -1)
        self.assertLess(idx, len(msgs))

    def test_respects_max_tokens(self):
        """Stops expanding when max_tokens is reached."""
        msgs = [_user(f"msg {i}") for i in range(10)]
        config = SessionMemoryCompactConfig(
            min_tokens=100_000,
            min_text_block_messages=100,
            max_tokens=50,
        )
        idx = calculate_messages_to_keep_index(msgs, 0, config)
        self.assertGreaterEqual(idx, 0)

    def test_preserves_tool_pairs(self):
        """Result doesn't split tool_use/tool_result pairs."""
        msgs = [
            _user("hello"),
            _assistant_tool("t1"),
            _user_result("t1"),
            _assistant("done"),
        ]
        idx = calculate_messages_to_keep_index(msgs, 0)
        self.assertLessEqual(idx, 1)


class TestAdjustIndex(unittest.TestCase):
    """Tests for adjust_index_to_preserve_api_invariants()."""

    def test_no_adjustment_needed(self):
        """Split between user and assistant doesn't need adjustment."""
        msgs = [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2")]
        self.assertEqual(adjust_index_to_preserve_api_invariants(msgs, 2), 2)

    def test_adjusts_to_not_split_tool_pair(self):
        """Split between tool_use and tool_result is moved earlier."""
        msgs = [
            _user("q1"),
            _assistant_tool("t1"),
            _user_result("t1"),
            _user("q2"),
        ]
        adjusted = adjust_index_to_preserve_api_invariants(msgs, 2)
        self.assertLessEqual(adjusted, 1)

    def test_index_zero(self):
        msgs = [_user("q1"), _assistant("a1")]
        self.assertEqual(adjust_index_to_preserve_api_invariants(msgs, 0), 0)

    def test_index_beyond_end(self):
        msgs = [_user("q1")]
        self.assertEqual(adjust_index_to_preserve_api_invariants(msgs, 5), 5)


class TestTrySessionMemoryCompaction(unittest.TestCase):
    """Tests for try_session_memory_compaction()."""

    def test_empty_messages(self):
        to_summarize, to_keep = try_session_memory_compaction([], 5)
        self.assertEqual(to_summarize, [])
        self.assertEqual(to_keep, [])

    def test_basic_split(self):
        msgs = [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2"),
                _user("q3"), _assistant("a3"), _user("q4"), _assistant("a4")]
        to_summarize, to_keep = try_session_memory_compaction(msgs, 4)
        self.assertEqual(len(to_summarize), 4)
        self.assertEqual(len(to_keep), 4)

    def test_preserves_tool_pairs(self):
        """Split adjusted to avoid breaking tool_use/tool_result."""
        msgs = [
            _user("q1"),
            _assistant_tool("t1"),
            _user_result("t1"),
            _user("q2"),
            _assistant("a2"),
        ]
        to_summarize, to_keep = try_session_memory_compaction(msgs, 2)
        summarized_ids = set()
        for m in to_summarize:
            if isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolUseBlock):
                        summarized_ids.add(b.id)
                    elif isinstance(b, ToolResultBlock):
                        summarized_ids.add(b.tool_use_id)
        kept_ids = set()
        for m in to_keep:
            if isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolUseBlock):
                        kept_ids.add(b.id)
                    elif isinstance(b, ToolResultBlock):
                        kept_ids.add(b.tool_use_id)
        self.assertEqual(summarized_ids & kept_ids, set())


if __name__ == "__main__":
    unittest.main()
