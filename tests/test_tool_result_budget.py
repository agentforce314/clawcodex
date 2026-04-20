"""
Tests for Layer 1: Tool Result Budget.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.tool_result_budget import (
    apply_tool_result_budget,
    cleanup_budget_dir,
    BudgetManifest,
    STORED_REFERENCE_TEMPLATE,
    DEFAULT_MAX_RESULT_TOKENS,
)


def _make_assistant_with_tool_use(tool_id: str, tool_name: str = "Read") -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input={"file_path": "test.txt"})],
    )


def _make_user_with_tool_result(tool_id: str, content: str) -> UserMessage:
    return UserMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=content)],
    )


class TestApplyToolResultBudget(unittest.TestCase):
    """Tests for apply_tool_result_budget()."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.budget_dir = Path(self.tmpdir.name) / "budget"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_small_results_left_in_place(self):
        """Results below the threshold are not offloaded."""
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", "small result"),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=10_000,
        )
        self.assertEqual(saved, 0)
        self.assertEqual(len(result), 2)

    def test_large_results_offloaded_to_disk(self):
        """Results above the threshold are written to disk."""
        large_content = "x" * 50_000  # ~12,500 tokens at 4 chars/token
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved, 0)

        # The tool result content should be a reference string
        user_msg = result[1]
        block = user_msg.content[0]
        self.assertIsInstance(block, ToolResultBlock)
        self.assertIn("[Tool result stored at:", block.content)

    def test_stored_file_contains_original_content(self):
        """The stored file on disk contains the original content."""
        large_content = "Hello " * 10_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)

        # Find the stored file
        stored_files = list(self.budget_dir.glob("result_*.txt"))
        self.assertEqual(len(stored_files), 1)
        self.assertEqual(stored_files[0].read_text(), large_content)

    def test_manifest_written(self):
        """A manifest file is created after offloading."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)

        manifest = BudgetManifest.load(self.budget_dir)
        self.assertEqual(len(manifest.stored), 1)
        self.assertEqual(manifest.stored[0].tool_use_id, "t1")

    def test_idempotent_on_already_stored(self):
        """Running twice doesn't re-store already-stored results."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        result1, saved1 = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved1, 0)

        # Run again with the already-replaced messages
        result2, saved2 = apply_tool_result_budget(
            result1, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertEqual(saved2, 0)

    def test_cleanup_removes_files(self):
        """cleanup_budget_dir() removes all stored files."""
        large_content = "x" * 50_000
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", large_content),
        ]
        apply_tool_result_budget(messages, self.budget_dir, max_result_tokens=1_000)
        self.assertTrue(self.budget_dir.exists())

        cleanup_budget_dir(self.budget_dir)
        self.assertFalse(self.budget_dir.exists())

    def test_mixed_small_and_large_results(self):
        """Only large results are offloaded; small ones stay."""
        messages = [
            _make_assistant_with_tool_use("t1"),
            _make_user_with_tool_result("t1", "small"),
            _make_assistant_with_tool_use("t2"),
            _make_user_with_tool_result("t2", "y" * 50_000),
        ]
        result, saved = apply_tool_result_budget(
            messages, self.budget_dir, max_result_tokens=1_000,
        )
        self.assertGreater(saved, 0)

        # First result unchanged
        self.assertEqual(result[1].content[0].content, "small")
        # Second result replaced
        self.assertIn("[Tool result stored at:", result[3].content[0].content)

    def test_empty_messages(self):
        """Empty message list returns empty."""
        result, saved = apply_tool_result_budget([], self.budget_dir)
        self.assertEqual(result, [])
        self.assertEqual(saved, 0)


if __name__ == "__main__":
    unittest.main()
