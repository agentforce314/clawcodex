"""Tests that the auto-memory section is wired into the system prompt.

Both the default path (section slot 25 in build_full_system_prompt) and
the SDK custom-prompt branch gated on has_auto_mem_path_override.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DefaultSystemPromptHookupTest(unittest.TestCase):
    def setUp(self):
        self._saved_override = os.environ.get(
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"
        )
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name

    def tearDown(self):
        if self._saved_override is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = (
                self._saved_override
            )
        self._tmp.cleanup()

    def test_memory_section_present_in_default_prompt(self):
        from src.context_system.prompt_assembly import (
            build_full_system_prompt,
        )

        prompt = build_full_system_prompt(use_cache=False)
        # Eval-validated section headers from memory_types.py
        self.assertIn("# auto memory", prompt)
        self.assertIn("## How to save memories", prompt)
        # Frontmatter contract reaches the model
        self.assertIn("type: {{user, feedback, project, reference}}", prompt)


class SdkCustomPromptHookupTest(unittest.TestCase):
    def setUp(self):
        self._saved_override = os.environ.get(
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"
        )
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        if self._saved_override is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = (
                self._saved_override
            )
        self._tmp.cleanup()

    def test_no_override_no_memory_in_custom_prompt(self):
        os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        from src.context_system.prompt_assembly import (
            build_full_system_prompt,
        )
        prompt = build_full_system_prompt(
            custom_system_prompt="Custom prompt body",
            use_cache=False,
        )
        self.assertIn("Custom prompt body", prompt)
        self.assertNotIn("# auto memory", prompt)

    def test_override_set_injects_memory_after_custom_prompt(self):
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name
        from src.context_system.prompt_assembly import (
            build_full_system_prompt,
        )
        prompt = build_full_system_prompt(
            custom_system_prompt="Custom prompt body",
            append_system_prompt="APPEND",
            use_cache=False,
        )
        self.assertIn("Custom prompt body", prompt)
        self.assertIn("# auto memory", prompt)
        self.assertIn("APPEND", prompt)
        # Order: custom < memory < append
        custom_idx = prompt.index("Custom prompt body")
        mem_idx = prompt.index("# auto memory")
        append_idx = prompt.index("APPEND")
        self.assertLess(custom_idx, mem_idx)
        self.assertLess(mem_idx, append_idx)


class MemorySectionContentTest(unittest.TestCase):
    """If MEMORY.md has content, it should land in the prompt verbatim
    (subject to truncation)."""

    def setUp(self):
        self._saved_override = os.environ.get(
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"
        )
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._tmp.name
        Path(self._tmp.name, "MEMORY.md").write_text(
            "- [Project facts](project_kickoff.md) — kickoff on 2026-04-01\n",
            encoding="utf-8",
        )

    def tearDown(self):
        if self._saved_override is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = (
                self._saved_override
            )
        self._tmp.cleanup()

    def test_memory_md_content_in_prompt(self):
        from src.context_system.prompt_assembly import (
            build_full_system_prompt,
        )
        prompt = build_full_system_prompt(use_cache=False)
        self.assertIn("project_kickoff.md", prompt)
        self.assertIn("kickoff on 2026-04-01", prompt)


if __name__ == "__main__":
    unittest.main()
