"""
Tests for message grouping by API round.
"""

from __future__ import annotations

import unittest

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.grouping import group_messages_by_api_round, ApiRound


class TestGroupMessagesByApiRound(unittest.TestCase):
    """Tests for group_messages_by_api_round()."""

    def test_empty_list(self):
        result = group_messages_by_api_round([])
        self.assertEqual(result, [])

    def test_single_user_message(self):
        """A lone user message goes into a round with no assistant."""
        messages = [UserMessage(content="Hello")]
        result = group_messages_by_api_round(messages)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].assistant)
        self.assertEqual(len(result[0].tool_results), 1)

    def test_single_assistant_message(self):
        """A lone assistant message starts its own round."""
        messages = [AssistantMessage(content=[TextBlock(text="Hi")])]
        result = group_messages_by_api_round(messages)
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0].assistant)
        self.assertEqual(len(result[0].tool_results), 0)

    def test_basic_round(self):
        """Assistant + user tool result forms one round."""
        messages = [
            AssistantMessage(
                content=[ToolUseBlock(id="t1", name="Read", input={})],
            ),
            UserMessage(
                content=[ToolResultBlock(tool_use_id="t1", content="file data")],
            ),
        ]
        result = group_messages_by_api_round(messages)
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0].assistant)
        self.assertEqual(len(result[0].tool_results), 1)

    def test_multiple_rounds(self):
        """Multiple assistant turns create separate rounds."""
        messages = [
            AssistantMessage(content=[TextBlock(text="Turn 1")]),
            UserMessage(content="Result 1"),
            AssistantMessage(content=[TextBlock(text="Turn 2")]),
            UserMessage(content="Result 2"),
        ]
        result = group_messages_by_api_round(messages)
        self.assertEqual(len(result), 2)
        for r in result:
            self.assertIsNotNone(r.assistant)
            self.assertEqual(len(r.tool_results), 1)

    def test_leading_user_messages(self):
        """User messages before any assistant go into a pre-round."""
        messages = [
            UserMessage(content="First question"),
            UserMessage(content="Follow-up"),
            AssistantMessage(content=[TextBlock(text="Answer")]),
        ]
        result = group_messages_by_api_round(messages)
        self.assertEqual(len(result), 2)
        # First round: no assistant, 2 user messages
        self.assertIsNone(result[0].assistant)
        self.assertEqual(len(result[0].tool_results), 2)
        # Second round: assistant, no tool results
        self.assertIsNotNone(result[1].assistant)
        self.assertEqual(len(result[1].tool_results), 0)

    def test_api_round_messages_property(self):
        """The messages property returns assistant + tool results."""
        asst = AssistantMessage(content=[TextBlock(text="Hi")])
        user = UserMessage(content="Result")
        round_ = ApiRound(assistant=asst, tool_results=[user])
        self.assertEqual(len(round_.messages), 2)
        self.assertIs(round_.messages[0], asst)
        self.assertIs(round_.messages[1], user)

    def test_api_round_no_assistant(self):
        """Messages property works when assistant is None."""
        user = UserMessage(content="Hello")
        round_ = ApiRound(assistant=None, tool_results=[user])
        self.assertEqual(len(round_.messages), 1)
        self.assertIs(round_.messages[0], user)


if __name__ == "__main__":
    unittest.main()
