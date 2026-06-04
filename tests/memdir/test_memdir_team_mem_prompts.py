"""Tests for src/memdir/team_mem_prompts.py — combined memory prompt."""

from __future__ import annotations

import os
import tempfile
import unittest

from src.memdir.team_mem_prompts import (
    TYPES_SECTION_COMBINED,
    build_combined_memory_prompt,
)

_TRACKED_ENV = (
    "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE",
    "CLAUDE_CODE_TEAM_MEMORY",
)


class _EnvFixture(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _TRACKED_ENV}
        for k in _TRACKED_ENV:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class BuildCombinedMemoryPromptTest(_EnvFixture):
    def test_mentions_both_directories(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("private directory at", prompt)
        self.assertIn("shared team directory at", prompt)

    def test_includes_memory_scope_section(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("## Memory scope", prompt)
        self.assertIn("- private:", prompt)
        self.assertIn("- team:", prompt)

    def test_includes_scope_tags_from_combined_types(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("<scope>", prompt)
        self.assertIn("always private", prompt)
        self.assertIn("usually team", prompt)

    def test_includes_no_secrets_warning(self):
        prompt = build_combined_memory_prompt()
        self.assertIn(
            "MUST avoid saving sensitive data within shared team memories",
            prompt,
        )

    def test_two_step_save_default(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("Step 1", prompt)
        self.assertIn("Step 2", prompt)
        self.assertIn("Both `MEMORY.md` indexes are loaded", prompt)

    def test_skip_index_drops_two_step_language(self):
        prompt = build_combined_memory_prompt(skip_index=True)
        self.assertNotIn("**Step 1**", prompt)
        self.assertNotIn("**Step 2**", prompt)
        # Per-scope guidance still appears.
        self.assertIn("private or team", prompt)

    def test_extra_guidelines_appended(self):
        guidelines = ["EXTRA_GUIDELINE_SENTINEL_42"]
        prompt = build_combined_memory_prompt(extra_guidelines=guidelines)
        self.assertIn("EXTRA_GUIDELINE_SENTINEL_42", prompt)

    def test_empty_extra_guideline_skipped(self):
        prompt = build_combined_memory_prompt(extra_guidelines=["", None])  # type: ignore[list-item]
        # No exception; empty strings filtered.
        self.assertIn("Memory and other forms of persistence", prompt)

    def test_includes_trusting_recall_section(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("## Before recommending from memory", prompt)

    def test_includes_drift_caveat(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("Memory records can become stale over time", prompt)

    def test_includes_dirs_exist_guidance(self):
        prompt = build_combined_memory_prompt()
        self.assertIn("Both directories already exist", prompt)


class CombinedTypesSectionTest(unittest.TestCase):
    """Shape assertions for TYPES_SECTION_COMBINED.

    The COMBINED variant must include a ``<scope>`` tag for each of the
    four memory types and use team/private qualifiers in examples.
    """

    def test_lists_all_four_types(self):
        joined = "\n".join(TYPES_SECTION_COMBINED)
        for t in ("user", "feedback", "project", "reference"):
            self.assertIn(f"<name>{t}</name>", joined)

    def test_each_type_has_scope_tag(self):
        # One scope OPEN tag per type block (the intro sentence references
        # `<scope>` as a literal too — count only indented tag lines).
        scope_lines = [
            line
            for line in TYPES_SECTION_COMBINED
            if line.startswith("    <scope>")
        ]
        self.assertEqual(len(scope_lines), 4)

    def test_scope_values_present(self):
        joined = "\n".join(TYPES_SECTION_COMBINED)
        self.assertIn("always private", joined)
        self.assertIn("usually team", joined)
        # The feedback-type guidance is unique to COMBINED.
        self.assertIn("default to private", joined)

    def test_examples_use_private_team_qualifiers(self):
        joined = "\n".join(TYPES_SECTION_COMBINED)
        self.assertIn("[saves private user memory:", joined)
        self.assertIn("[saves team", joined)


class TeamMemoryDispatchTest(_EnvFixture):
    """``load_memory_prompt()`` switches to the combined prompt when
    ``CLAUDE_CODE_TEAM_MEMORY`` is set."""

    def test_flag_off_returns_single_directory_prompt(self):
        from src.memdir import load_memory_prompt

        prompt = load_memory_prompt()
        self.assertIsNotNone(prompt)
        # Single-directory header
        self.assertIn("# auto memory", prompt)
        # Combined prompt's header / no-secrets warning must NOT appear.
        self.assertNotIn("## Memory scope", prompt)
        self.assertNotIn(
            "MUST avoid saving sensitive data within shared team memories",
            prompt,
        )

    def test_flag_on_returns_combined_prompt(self):
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "1"
        from src.memdir import load_memory_prompt

        prompt = load_memory_prompt()
        self.assertIsNotNone(prompt)
        # Combined header is "# Memory" (not "# auto memory")
        self.assertIn("# Memory", prompt)
        self.assertIn("## Memory scope", prompt)
        self.assertIn(
            "MUST avoid saving sensitive data within shared team memories",
            prompt,
        )

    def test_flag_on_creates_team_directory(self):
        os.environ["CLAUDE_CODE_TEAM_MEMORY"] = "1"
        from pathlib import Path

        from src.memdir import get_team_mem_path, load_memory_prompt

        team_dir = get_team_mem_path().rstrip(os.sep)
        load_memory_prompt()
        self.assertTrue(
            Path(team_dir).is_dir(),
            f"expected {team_dir} to exist after load_memory_prompt()",
        )


if __name__ == "__main__":
    unittest.main()
