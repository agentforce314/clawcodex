"""Tests for message normalization to Anthropic-style API payloads."""

from __future__ import annotations

import unittest

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    AssistantMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    UserMessage,
    create_user_message,
    normalize_messages_for_api,
)


class TestMessageNormalization(unittest.TestCase):
    def test_normalize_typed_messages(self):
        messages = [
            create_user_message("hello"),
            AssistantMessage(
                content=[
                    TextBlock(text="I will read the file"),
                    ToolUseBlock(id="toolu_1", name="Read", input={"file_path": "README.md"}),
                ]
            ),
            create_user_message([ToolResultBlock(tool_use_id="toolu_1", content="done")]),
        ]

        normalized = normalize_messages_for_api(messages)

        self.assertEqual(len(normalized), 3)
        self.assertEqual(normalized[0], {"role": "user", "content": "hello"})
        self.assertEqual(normalized[1]["role"], "assistant")
        self.assertEqual(normalized[1]["content"][0]["type"], "text")
        self.assertEqual(normalized[1]["content"][1]["type"], "tool_use")
        self.assertEqual(normalized[2]["content"][0]["type"], "tool_result")

    def test_filters_progress_messages(self):
        normalized = normalize_messages_for_api([
            create_user_message("before"),
            ProgressMessage(content="working...", toolUseID="t1", parentToolUseID="t0"),
            AssistantMessage(content=[TextBlock(text="done")]),
        ])

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["role"], "user")
        self.assertEqual(normalized[1]["role"], "assistant")

    def test_filters_system_messages(self):
        normalized = normalize_messages_for_api([
            create_user_message("before"),
            SystemMessage(content="boundary", subtype="compact_boundary"),
            create_user_message("after"),
        ])

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["role"], "user")

    def test_filters_virtual_messages(self):
        normalized = normalize_messages_for_api([
            create_user_message("before"),
            create_user_message("virtual", isVirtual=True),
            create_user_message("after"),
        ])

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["role"], "user")

    def test_merges_consecutive_user_messages(self):
        normalized = normalize_messages_for_api([
            create_user_message("first"),
            create_user_message("second"),
        ])

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["role"], "user")
        self.assertEqual(len(normalized[0]["content"]), 2)

    def test_normalizes_legacy_message_objects(self):
        legacy = Message(
            role="assistant",
            content=[
                {"type": "text", "text": "legacy"},
                {"type": "tool_use", "id": "toolu_2", "name": "Grep", "input": {"query": "foo"}},
            ],
        )

        normalized = normalize_messages_for_api([legacy])

        self.assertEqual(normalized[0]["role"], "assistant")
        self.assertEqual(normalized[0]["content"][0]["text"], "legacy")
        self.assertEqual(normalized[0]["content"][1]["name"], "Grep")

    def test_preserves_assistant_reasoning_content(self):
        assistant = AssistantMessage(content=[TextBlock(text="working")])
        assistant.reasoning_content = "internal reasoning trace"  # type: ignore[attr-defined]

        normalized = normalize_messages_for_api([assistant])

        self.assertEqual(normalized[0]["role"], "assistant")
        self.assertEqual(normalized[0]["reasoning_content"], "internal reasoning trace")


if __name__ == "__main__":
    unittest.main()
