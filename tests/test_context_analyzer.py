"""
Tests for context analysis.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestContextWindowForModel(unittest.TestCase):
    """Tests for get_context_window_for_model()."""

    def test_claude_sonnet_4_6(self):
        """Claude Sonnet 4-6 returns 200k."""
        from src.context_system.context_analyzer import get_context_window_for_model
        result = get_context_window_for_model("claude-sonnet-4-6")
        self.assertEqual(result, 200_000)

    def test_gpt_4o(self):
        """GPT-4o returns 128k."""
        from src.context_system.context_analyzer import get_context_window_for_model
        result = get_context_window_for_model("gpt-4o")
        self.assertEqual(result, 128_000)

    def test_unknown_model_defaults(self):
        """Unknown model returns default 200k."""
        from src.context_system.context_analyzer import get_context_window_for_model
        result = get_context_window_for_model("unknown-model-xyz")
        self.assertEqual(result, 200_000)


class TestAnalyzeContext(unittest.TestCase):
    """Tests for analyze_context()."""

    def test_empty_inputs(self):
        """Empty inputs returns reasonable defaults."""
        from src.context_system.context_analyzer import analyze_context, get_context_window_for_model
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[],
            claude_md_content="",
        )
        self.assertEqual(result.model, "claude-sonnet-4-6")
        self.assertEqual(result.max_tokens, 200_000)
        # With all empty inputs, total_tokens is 0 (no content), but max_tokens is preserved
        self.assertEqual(result.total_tokens, 0)

    def test_system_prompt_tokens(self):
        """System prompt contributes to total."""
        from src.context_system.context_analyzer import analyze_context
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="You are a helpful assistant. " * 50,  # ~500 chars
            tool_schemas=[],
            claude_md_content="",
        )
        system_cat = next((c for c in result.categories if c.name == "System prompt"), None)
        self.assertIsNotNone(system_cat)
        self.assertGreater(system_cat.tokens, 0)

    def test_tool_schemas_tokens(self):
        """Tool schemas contribute to total."""
        from src.context_system.context_analyzer import analyze_context
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[
                {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}},
                {"name": "Write", "description": "Write a file", "input_schema": {"type": "object"}},
            ],
            claude_md_content="",
        )
        tool_cat = next((c for c in result.categories if c.name == "System tools"), None)
        self.assertIsNotNone(tool_cat)
        self.assertGreater(tool_cat.tokens, 0)

    def test_message_tokens(self):
        """Messages contribute to total."""
        from src.context_system.context_analyzer import analyze_context
        result = analyze_context(
            conversation_api_messages=[
                {"role": "user", "content": "Hello world"},
                {"role": "assistant", "content": "Hi there!"},
            ],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[],
            claude_md_content="",
        )
        msg_cat = next((c for c in result.categories if c.name == "Messages"), None)
        self.assertIsNotNone(msg_cat)
        self.assertGreater(msg_cat.tokens, 0)

    def test_free_space_calculation(self):
        """Free space = max_tokens - total_usage."""
        from src.context_system.context_analyzer import analyze_context
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="x",  # minimal
            tool_schemas=[],
            claude_md_content="",
        )
        free_cat = next((c for c in result.categories if c.name == "Free space"), None)
        self.assertIsNotNone(free_cat)
        self.assertGreater(free_cat.tokens, 0)
        # Free space should be less than max
        self.assertLess(free_cat.tokens, result.max_tokens)

    def test_api_usage_overrides_total(self):
        """API usage, when provided, overrides estimated total."""
        from src.context_system.context_analyzer import analyze_context
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[],
            claude_md_content="",
            api_usage={
                "input_tokens": 50000,
                "output_tokens": 10000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        # Total should reflect the API usage sum
        self.assertEqual(result.total_tokens, 50000)


class TestFormatContextAsMarkdown(unittest.TestCase):
    """Tests for format_context_as_markdown()."""

    def test_produces_valid_markdown(self):
        """Output contains expected Markdown structure."""
        from src.context_system.context_analyzer import analyze_context, format_context_as_markdown
        result = analyze_context(
            conversation_api_messages=[
                {"role": "user", "content": "Hello"},
            ],
            model="claude-sonnet-4-6",
            system_prompt="You are helpful.",
            tool_schemas=[],
            claude_md_content="",
        )
        markdown = format_context_as_markdown(result)
        self.assertIn("## Context Usage", markdown)
        self.assertIn("**Model:**", markdown)
        self.assertIn("**Tokens:**", markdown)
        self.assertIn("| Category |", markdown)

    def test_shows_system_prompt_category(self):
        """System prompt appears in table."""
        from src.context_system.context_analyzer import analyze_context, format_context_as_markdown
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="You are a helpful assistant. " * 100,
            tool_schemas=[],
            claude_md_content="",
        )
        markdown = format_context_as_markdown(result)
        self.assertIn("System prompt", markdown)

    def test_shows_memory_files(self):
        """Memory files section appears when CLAUDE.md is present."""
        from src.context_system.context_analyzer import analyze_context, format_context_as_markdown
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[],
            claude_md_content="# Project\n\nThis is a project.",
        )
        markdown = format_context_as_markdown(result)
        self.assertIn("### Memory Files", markdown)
        self.assertIn("CLAUDE.md", markdown)

    def test_shows_api_usage(self):
        """API usage section appears when usage data is provided."""
        from src.context_system.context_analyzer import analyze_context, format_context_as_markdown
        result = analyze_context(
            conversation_api_messages=[],
            model="claude-sonnet-4-6",
            system_prompt="",
            tool_schemas=[],
            claude_md_content="",
            api_usage={
                "input_tokens": 10000,
                "output_tokens": 5000,
                "cache_creation_input_tokens": 2000,
                "cache_read_input_tokens": 500,
            }
        )
        markdown = format_context_as_markdown(result)
        self.assertIn("### API Usage", markdown)
        self.assertIn("10,000", markdown)


if __name__ == "__main__":
    unittest.main()
