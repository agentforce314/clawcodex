"""Tests for src/memdir/memory_types.py — Slice A taxonomy and prompt text."""

from __future__ import annotations

import unittest

from src.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    MEMORY_TYPES,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
    parse_memory_type,
)


class TaxonomyTest(unittest.TestCase):
    def test_four_types(self):
        self.assertEqual(
            MEMORY_TYPES, ("user", "feedback", "project", "reference")
        )

    def test_parse_known(self):
        for t in MEMORY_TYPES:
            self.assertEqual(parse_memory_type(t), t)

    def test_parse_unknown_returns_none(self):
        self.assertIsNone(parse_memory_type("invalid"))
        self.assertIsNone(parse_memory_type(""))
        self.assertIsNone(parse_memory_type(None))
        self.assertIsNone(parse_memory_type(123))


class PromptTextTest(unittest.TestCase):
    def test_individual_section_lists_all_types(self):
        joined = "\n".join(TYPES_SECTION_INDIVIDUAL)
        for t in MEMORY_TYPES:
            self.assertIn(f"<name>{t}</name>", joined)

    def test_what_not_to_save_includes_derivability_rule(self):
        joined = "\n".join(WHAT_NOT_TO_SAVE_SECTION)
        self.assertIn("Code patterns", joined)
        self.assertIn("Git history", joined)
        # The eval-validated explicit-save override line
        self.assertIn("explicitly asks you to save", joined)

    def test_when_to_access_has_drift_caveat(self):
        joined = "\n".join(WHEN_TO_ACCESS_SECTION)
        self.assertIn("Memory records can become stale", joined)
        self.assertIn("ignore", joined)

    def test_trusting_recall_is_action_cue_header(self):
        joined = "\n".join(TRUSTING_RECALL_SECTION)
        # Eval-validated header — "Before recommending" not "Trusting"
        self.assertIn("## Before recommending from memory", joined)

    def test_frontmatter_example_lists_all_types(self):
        joined = "\n".join(MEMORY_FRONTMATTER_EXAMPLE)
        # All four types should appear in the {{...}} placeholder
        for t in MEMORY_TYPES:
            self.assertIn(t, joined)


if __name__ == "__main__":
    unittest.main()
