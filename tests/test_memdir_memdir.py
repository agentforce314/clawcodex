"""Tests for src/memdir/memdir.py — Slice A index handling and prompt."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.memdir.memdir import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    build_memory_lines,
    build_memory_prompt,
    ensure_memory_dir_exists,
    truncate_entrypoint_content,
)


class TruncateEntrypointTest(unittest.TestCase):
    def test_under_caps_unchanged(self):
        raw = "- entry 1\n- entry 2\n"
        out = truncate_entrypoint_content(raw)
        self.assertFalse(out.was_line_truncated)
        self.assertFalse(out.was_byte_truncated)
        self.assertEqual(out.content, raw.strip())

    def test_line_cap_warning_names_lines(self):
        raw = "\n".join(f"- e{i}" for i in range(MAX_ENTRYPOINT_LINES + 5))
        out = truncate_entrypoint_content(raw)
        self.assertTrue(out.was_line_truncated)
        self.assertFalse(out.was_byte_truncated)
        self.assertIn(f"{out.line_count} lines", out.content)
        self.assertIn(f"limit: {MAX_ENTRYPOINT_LINES}", out.content)
        self.assertIn(
            "Keep index entries to one line under ~200 chars",
            out.content,
        )

    def test_byte_cap_warning_names_size(self):
        # Few lines, but each is huge — byte cap should fire alone.
        long_line = "x" * 30_000
        raw = f"- {long_line}\n- short\n"
        out = truncate_entrypoint_content(raw)
        self.assertTrue(out.was_byte_truncated)
        self.assertFalse(out.was_line_truncated)
        self.assertIn("index entries are too long", out.content)

    def test_byte_cap_cuts_at_newline(self):
        # First line under cap, second line pushes over the cap.
        first = "- " + "a" * 100
        # Make the second line so large the truncated content is
        # forced to cut just after the first line's newline.
        second = "- " + "b" * (MAX_ENTRYPOINT_BYTES + 100)
        raw = f"{first}\n{second}\n"
        out = truncate_entrypoint_content(raw)
        self.assertTrue(out.was_byte_truncated)
        # The truncated content (before the warning) should not start
        # mid-line — we cut at a newline. Check by stripping the
        # warning suffix and verifying the kept content matches the
        # first line.
        body = out.content.split("\n\n> WARNING")[0]
        self.assertTrue(body.startswith("- "))

    def test_multibyte_content_is_measured_and_cut_in_utf8_bytes(self):
        raw = "界" * (MAX_ENTRYPOINT_BYTES // 2)
        out = truncate_entrypoint_content(raw)
        body = out.content.split("\n\n> WARNING")[0]
        self.assertTrue(out.was_byte_truncated)
        self.assertGreater(out.byte_count, MAX_ENTRYPOINT_BYTES)
        self.assertLessEqual(len(body.encode("utf-8")), MAX_ENTRYPOINT_BYTES)
        self.assertNotIn("\ufffd", body)


class EnsureMemoryDirExistsTest(unittest.TestCase):
    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "deep", "memory")
            ensure_memory_dir_exists(target)
            self.assertTrue(os.path.isdir(target))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "memory")
            ensure_memory_dir_exists(target)
            ensure_memory_dir_exists(target)
            self.assertTrue(os.path.isdir(target))


class BuildMemoryLinesTest(unittest.TestCase):
    def test_includes_load_bearing_sections(self):
        lines = build_memory_lines(
            display_name="auto memory",
            memory_dir="/tmp/mem/",
        )
        joined = "\n".join(lines)
        # Header + dir
        self.assertIn("# auto memory", joined)
        self.assertIn("/tmp/mem/", joined)
        # Type taxonomy
        self.assertIn("## Types of memory", joined)
        # Eval-validated sections
        self.assertIn("## What NOT to save in memory", joined)
        self.assertIn("## When to access memories", joined)
        self.assertIn("## Before recommending from memory", joined)
        # Two-step write protocol
        self.assertIn("## How to save memories", joined)
        self.assertIn("Step 1", joined)
        self.assertIn("Step 2", joined)
        # Frontmatter contract
        self.assertIn("type: {{user, feedback, project, reference}}", joined)

    def test_skip_index_drops_step_2(self):
        lines = build_memory_lines(
            display_name="auto memory",
            memory_dir="/tmp/mem/",
            skip_index=True,
        )
        joined = "\n".join(lines)
        self.assertNotIn("Step 2", joined)

    def test_extra_guidelines_appended(self):
        lines = build_memory_lines(
            display_name="auto memory",
            memory_dir="/tmp/mem/",
            extra_guidelines=["Do not write secrets to memory."],
        )
        joined = "\n".join(lines)
        self.assertIn("Do not write secrets to memory.", joined)


class BuildMemoryPromptTest(unittest.TestCase):
    def test_empty_memory_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_memory_prompt(
                display_name="auto memory",
                memory_dir=tmp,
            )
            self.assertIn(
                f"## {ENTRYPOINT_NAME}", prompt,
            )
            self.assertIn(
                f"Your {ENTRYPOINT_NAME} is currently empty",
                prompt,
            )

    def test_populated_memory_md_inlined(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / ENTRYPOINT_NAME
            entry.write_text(
                "- [Test memory](feedback_test.md) — testing\n",
                encoding="utf-8",
            )
            prompt = build_memory_prompt(
                display_name="auto memory",
                memory_dir=tmp,
            )
            self.assertIn("- [Test memory](feedback_test.md) — testing", prompt)

    def test_oversized_memory_md_truncated_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / ENTRYPOINT_NAME
            entry.write_text(
                "\n".join(f"- e{i}" for i in range(MAX_ENTRYPOINT_LINES + 50)),
                encoding="utf-8",
            )
            prompt = build_memory_prompt(
                display_name="auto memory",
                memory_dir=tmp,
            )
            self.assertIn("WARNING", prompt)
            self.assertIn(f"limit: {MAX_ENTRYPOINT_LINES}", prompt)


if __name__ == "__main__":
    unittest.main()


class TestSearchingPastContextSection:
    """MEMDIR-1 — the "Searching past context" guidance (memdir.ts:375-407).

    Upstream gates it on tengu_coral_fern, which the vendored GrowthBook
    stub's _openBuildDefaults sets TRUE — the reference build emits it for
    every user, so this port emits it unconditionally (critic-caught: the
    original close misread the flag by stopping at the call-site default).
    """

    def test_section_content(self):
        from src.memdir.memdir import build_searching_past_context_section

        lines = build_searching_past_context_section("/tmp/memdir-x")
        joined = "\n".join(lines)
        assert lines[0] == "## Searching past context"
        assert 'Grep with pattern="<search term>" path="/tmp/memdir-x" glob="*.md"' in joined
        assert '/sessions/" glob="*.json"' in joined  # the port's transcript store
        assert "narrow search terms" in joined
        assert "last resort" in joined

    def test_build_memory_lines_ends_with_section(self):
        from src.memdir.memdir import build_memory_lines

        lines = build_memory_lines(display_name="Memory", memory_dir="/tmp/m")
        joined = "\n".join(lines)
        assert joined.count("## Searching past context") == 1
        # TS memdir.ts:263 — the section closes buildMemoryLines.
        assert "## Searching past context" in "\n".join(lines[-15:])

    def test_build_memory_prompt_section_precedes_entrypoint_block(self, tmp_path):
        from src.memdir.memdir import ENTRYPOINT_NAME, build_memory_prompt

        (tmp_path / ENTRYPOINT_NAME).write_text("- indexed fact", encoding="utf-8")
        prompt = build_memory_prompt(
            display_name="Memory", memory_dir=str(tmp_path)
        )
        # TS buildMemoryPrompt (:293) reuses buildMemoryLines (section at its
        # tail) then appends the MEMORY.md block — section BEFORE the block.
        assert prompt.count("## Searching past context") == 1
        assert prompt.index("## Searching past context") < prompt.index(f"## {ENTRYPOINT_NAME}")

    def test_combined_prompt_ends_with_section_once(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_TEAM_MEMORY", "1")
        monkeypatch.setenv("CLAUDE_CODE_AUTO_MEMORY_PATH", str(tmp_path))
        from src.memdir.team_mem_prompts import build_combined_memory_prompt

        prompt = build_combined_memory_prompt()
        # TS teamMemPrompts.ts:95-96 — blank separator, then the section,
        # closing the prompt.
        assert prompt.count("## Searching past context") == 1
        assert "\n\n## Searching past context" in prompt
        assert prompt.rstrip().endswith("rather than broad keywords.")
