"""
Tests for microcompact message preprocessing.
"""

from __future__ import annotations

import unittest


class TestStripImagesFromMessages(unittest.TestCase):
    """Tests for strip_images_from_messages()."""

    def test_passes_through_non_user_messages(self):
        """Non-user messages pass through unchanged."""
        from src.context_system.microcompact import strip_images_from_messages
        messages = [
            {"role": "assistant", "content": "Hello"},
            {"role": "system", "content": "System prompt"},
        ]
        result = strip_images_from_messages(messages)
        self.assertEqual(result, messages)

    def test_passes_through_user_with_string_content(self):
        """User message with string content passes through."""
        from src.context_system.microcompact import strip_images_from_messages
        messages = [{"role": "user", "content": "Hello world"}]
        result = strip_images_from_messages(messages)
        self.assertEqual(result, messages)

    def test_replaces_image_block_with_text(self):
        """Image blocks are replaced with [image] text."""
        from src.context_system.microcompact import strip_images_from_messages
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "xyz"}},
                ]
            }
        ]
        result = strip_images_from_messages(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], [{"type": "text", "text": "[image]"}])

    def test_replaces_document_block_with_text(self):
        """Document blocks are replaced with [document] text."""
        from src.context_system.microcompact import strip_images_from_messages
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "url", "url": "http://example.com/doc.pdf"}},
                ]
            }
        ]
        result = strip_images_from_messages(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], [{"type": "text", "text": "[document]"}])

    def test_strips_nested_images_in_tool_result(self):
        """Images nested in tool_result content are stripped."""
        from src.context_system.microcompact import strip_images_from_messages
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "abc",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "xyz"}},
                            {"type": "text", "text": "Some text"},
                        ]
                    }
                ]
            }
        ]
        result = strip_images_from_messages(messages)
        self.assertEqual(len(result), 1)
        # The outer content has 1 block (tool_result), but its nested content has 2 items
        self.assertEqual(len(result[0]["content"]), 1)
        nested = result[0]["content"][0]["content"]
        self.assertEqual(len(nested), 2)
        self.assertEqual(nested[0], {"type": "text", "text": "[image]"})
        self.assertEqual(nested[1], {"type": "text", "text": "Some text"})


class TestMicrocompactMessages(unittest.TestCase):
    """Tests for microcompact_messages()."""

    def test_no_messages_returns_unchanged(self):
        """Empty message list returns unchanged."""
        from src.context_system.microcompact import microcompact_messages
        messages = []
        result, saved = microcompact_messages(messages)
        self.assertEqual(result, [])
        self.assertEqual(saved, 0)

    def test_no_tool_results_returns_unchanged(self):
        """Messages without tool results pass through unchanged."""
        from src.context_system.microcompact import microcompact_messages
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result, saved = microcompact_messages(messages)
        self.assertEqual(result, messages)
        self.assertEqual(saved, 0)

    def test_keeps_recent_tool_results(self):
        """Recent tool results (within keep_recent) are not cleared."""
        from src.context_system.microcompact import microcompact_messages, CLEARED_MESSAGE
        messages = [
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "a.txt"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool1", "content": "Content of a.txt is 1000 lines long..."},
                ]
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool2", "name": "Read", "input": {"file_path": "b.txt"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool2", "content": "Content of b.txt is 500 lines..."},
                ]
            },
        ]
        # keep_recent=3, we have 2 tool calls, none should be cleared
        result, saved = microcompact_messages(messages, keep_recent=3)
        self.assertEqual(saved, 0)
        # Check tool results are unchanged
        for msg in result:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if block.get("type") == "tool_result":
                        self.assertNotEqual(block.get("content"), CLEARED_MESSAGE)

    def test_clears_old_tool_results(self):
        """Old tool results beyond keep_recent are cleared."""
        from src.context_system.microcompact import microcompact_messages, CLEARED_MESSAGE
        messages = [
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "old1.txt"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool1", "content": "Old large content..."},
                ]
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool2", "name": "Read", "input": {"file_path": "old2.txt"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool2", "content": "More old large content..."},
                ]
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool3", "name": "Read", "input": {"file_path": "recent.txt"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool3", "content": "Recent content"},
                ]
            },
        ]
        # keep_recent=1, so tool1 and tool2 should be cleared
        result, saved = microcompact_messages(messages, keep_recent=1)
        self.assertGreater(saved, 0)

        # Check that old tool results are cleared
        cleared_count = 0
        for msg in result:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if block.get("type") == "tool_result":
                        if block.get("content") == CLEARED_MESSAGE:
                            cleared_count += 1
        self.assertEqual(cleared_count, 2)

    def test_non_compactable_tools_not_cleared(self):
        """Tool results from non-compactable tools are not cleared."""
        from src.context_system.microcompact import microcompact_messages, CLEARED_MESSAGE
        messages = [
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool1", "name": "Write", "input": {"file_path": "a.txt", "content": "x" * 100}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool1", "content": "Written successfully"},
                ]
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tool2", "name": "NotCompactable", "input": {}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool2", "content": "Not compactable result"},
                ]
            },
        ]
        # NotCompactable is not in COMPACTABLE_TOOL_NAMES, so all should remain
        result, saved = microcompact_messages(messages, keep_recent=1)
        self.assertEqual(saved, 0)


if __name__ == "__main__":
    unittest.main()
