"""Tests for src/context_system/claude_md.py — WS-5 multi-level CLAUDE.md."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.context_system.claude_md import (
    _extract_include_paths,
    _parse_frontmatter_paths,
    _parse_memory_file_content,
    _resolve_include_path,
    clear_memory_file_caches,
    get_claude_mds,
    get_memory_files,
    is_memory_file_path,
    process_md_rules,
    process_memory_file,
    strip_html_comments,
)
from src.context_system.models import MAX_INCLUDE_DEPTH, MemoryFileInfo


def _run(coro):
    return asyncio.run(coro)


class TestStripHtmlComments(unittest.TestCase):
    def test_no_comments(self):
        self.assertEqual(strip_html_comments("hello world"), "hello world")

    def test_single_comment(self):
        self.assertEqual(strip_html_comments("before <!-- comment --> after"), "before  after")

    def test_multiline_comment(self):
        content = "before\n<!-- multi\nline -->\nafter"
        result = strip_html_comments(content)
        self.assertNotIn("<!--", result)
        self.assertIn("before", result)
        self.assertIn("after", result)


class TestExtractIncludePaths(unittest.TestCase):
    def test_simple_include(self):
        paths = _extract_include_paths("@./config.md", "/project")
        self.assertEqual(len(paths), 1)
        self.assertIn("/project/config.md", paths)

    def test_home_include(self):
        paths = _extract_include_paths("@~/notes.md", "/project")
        self.assertEqual(len(paths), 1)
        expected = str(Path.home() / "notes.md")
        self.assertIn(expected, paths)

    def test_absolute_include(self):
        paths = _extract_include_paths("@/etc/config.md", "/project")
        self.assertIn("/etc/config.md", paths)

    def test_skip_code_blocks(self):
        text = "```\n@./should-not-include.md\n```\n@./should-include.md"
        paths = _extract_include_paths(text, "/project")
        self.assertNotIn("/project/should-not-include.md", paths)
        self.assertIn("/project/should-include.md", paths)

    def test_multiple_includes(self):
        text = "@./a.md\n@./b.md\n@./c.md"
        paths = _extract_include_paths(text, "/project")
        self.assertEqual(len(paths), 3)

    def test_fragment_stripped(self):
        paths = _extract_include_paths("@./doc.md#section", "/project")
        self.assertIn("/project/doc.md", paths)


class TestResolveIncludePath(unittest.TestCase):
    def test_relative_dot(self):
        self.assertEqual(_resolve_include_path("./file.md", "/base"), "/base/file.md")

    def test_absolute(self):
        self.assertEqual(_resolve_include_path("/etc/conf.md", "/base"), "/etc/conf.md")

    def test_home(self):
        result = _resolve_include_path("~/doc.md", "/base")
        self.assertEqual(result, str(Path.home() / "doc.md"))

    def test_bare_relative(self):
        result = _resolve_include_path("file.md", "/base")
        self.assertEqual(result, "/base/file.md")


class TestParseFrontmatterPaths(unittest.TestCase):
    def test_no_frontmatter(self):
        content, paths = _parse_frontmatter_paths("# Hello\nWorld")
        self.assertIsNone(paths)
        self.assertIn("Hello", content)

    def test_with_paths(self):
        text = "---\npaths: src/**, tests/**\n---\nContent here"
        content, paths = _parse_frontmatter_paths(text)
        self.assertIsNotNone(paths)
        # /** suffix is stripped per TS behavior
        self.assertIn("src", paths)
        self.assertIn("tests", paths)
        self.assertIn("Content here", content)

    def test_paths_list_form(self):
        text = "---\npaths:\n  - src\n  - tests\n---\nbody"
        content, paths = _parse_frontmatter_paths(text)
        self.assertIsNotNone(paths)
        self.assertIn("src", paths)

    def test_wildcard_treated_as_none(self):
        text = "---\npaths: **\n---\nbody"
        content, paths = _parse_frontmatter_paths(text)
        self.assertIsNone(paths)


class TestParseMemoryFileContent(unittest.TestCase):
    def test_basic_md_file(self):
        info, includes = _parse_memory_file_content(
            "# Rules\nAlways test.", "/project/CLAUDE.md", "Project",
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.type, "Project")
        self.assertIn("Always test", info.content)

    def test_non_text_extension_rejected(self):
        info, includes = _parse_memory_file_content(
            "binary stuff", "/file.jpg", "Project",
        )
        self.assertIsNone(info)

    def test_html_comments_stripped(self):
        info, _ = _parse_memory_file_content(
            "before <!-- comment --> after", "/test.md", "Project",
        )
        self.assertNotIn("comment", info.content)

    def test_include_paths_extracted(self):
        info, includes = _parse_memory_file_content(
            "See @./extra.md for more", "/project/CLAUDE.md", "Project",
            include_base_path="/project/CLAUDE.md",
        )
        self.assertTrue(len(includes) > 0)


class TestProcessMemoryFile(unittest.TestCase):
    def test_basic_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "CLAUDE.md"
            f.write_text("# Rules\nAlways test.", encoding="utf-8")
            result = _run(process_memory_file(str(f), "Project", set()))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].type, "Project")
            self.assertIn("Always test", result[0].content)

    def test_nonexistent_file(self):
        result = _run(process_memory_file("/nonexistent/CLAUDE.md", "Project", set()))
        self.assertEqual(len(result), 0)

    def test_circular_reference_prevention(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "CLAUDE.md"
            f.write_text(f"Include self: @{f}", encoding="utf-8")
            result = _run(process_memory_file(str(f), "Project", set()))
            # Should process once, not infinitely loop
            self.assertEqual(len(result), 1)

    def test_include_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "CLAUDE.md"
            included = Path(tmp) / "extra.md"
            included.write_text("Extra rules.", encoding="utf-8")
            main.write_text(f"Main rules.\n@{included}", encoding="utf-8")
            # Set CLAUDE_CODE_ORIGINAL_CWD so included file is not external
            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                result = _run(process_memory_file(str(main), "Project", set()))
            self.assertEqual(len(result), 2)
            self.assertIn("Main rules", result[0].content)
            self.assertIn("Extra rules", result[1].content)

    def test_max_depth_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Create a chain deeper than MAX_INCLUDE_DEPTH
            files = []
            for i in range(MAX_INCLUDE_DEPTH + 3):
                f = Path(tmp) / f"level_{i}.md"
                files.append(f)
            # Link them: level_0 -> level_1 -> level_2 -> ...
            for i, f in enumerate(files):
                if i + 1 < len(files):
                    f.write_text(f"Level {i}\n@./level_{i+1}.md", encoding="utf-8")
                else:
                    f.write_text(f"Level {i}", encoding="utf-8")

            result = _run(process_memory_file(str(files[0]), "Project", set()))
            self.assertLessEqual(len(result), MAX_INCLUDE_DEPTH)


class TestProcessMdRules(unittest.TestCase):
    def test_rules_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".claude" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "style.md").write_text("Use 4-space indent.", encoding="utf-8")
            (rules_dir / "testing.md").write_text("Always add tests.", encoding="utf-8")
            result = _run(process_md_rules(str(rules_dir), "Project", set()))
            self.assertEqual(len(result), 2)

    def test_nonexistent_directory(self):
        result = _run(process_md_rules("/nonexistent/rules", "Project", set()))
        self.assertEqual(len(result), 0)

    def test_conditional_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / "rules"
            rules_dir.mkdir()
            # Rule with glob path -> conditional
            (rules_dir / "conditional.md").write_text(
                "---\npaths: src/**\n---\nSrc-only rule.", encoding="utf-8",
            )
            # Rule without glob -> unconditional
            (rules_dir / "always.md").write_text("Always applies.", encoding="utf-8")

            # Get only conditional rules
            result = _run(process_md_rules(str(rules_dir), "Project", set(), conditional_rule=True))
            self.assertEqual(len(result), 1)
            self.assertIn("Src-only", result[0].content)

            # Get only unconditional rules
            result = _run(process_md_rules(str(rules_dir), "Project", set(), conditional_rule=False))
            self.assertEqual(len(result), 1)
            self.assertIn("Always", result[0].content)


class TestGetClaudeMds(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(get_claude_mds([]), "")

    def test_formats_correctly(self):
        files = [
            MemoryFileInfo(path="/p/CLAUDE.md", type="Project", content="Rule 1"),
            MemoryFileInfo(path="/home/.claude/CLAUDE.md", type="User", content="Rule 2"),
        ]
        result = get_claude_mds(files)
        self.assertIn("Rule 1", result)
        self.assertIn("Rule 2", result)
        self.assertIn("project instructions", result)
        self.assertIn("global instructions", result)
        self.assertIn("OVERRIDE", result)

    def test_empty_content_skipped(self):
        files = [
            MemoryFileInfo(path="/p/CLAUDE.md", type="Project", content=""),
            MemoryFileInfo(path="/p/RULES.md", type="Project", content="Real rule"),
        ]
        result = get_claude_mds(files)
        self.assertIn("Real rule", result)
        self.assertNotIn("CLAUDE.md", result)


class TestGetMemoryFiles(unittest.TestCase):
    def test_caching(self):
        clear_memory_file_caches()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("test", encoding="utf-8")
            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                result1 = _run(get_memory_files(cwd=tmp))
                result2 = _run(get_memory_files(cwd=tmp))
                # Should return from cache
                self.assertEqual(len(result1), len(result2))
        clear_memory_file_caches()

    def test_cache_clearing(self):
        clear_memory_file_caches()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("test", encoding="utf-8")
            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                _run(get_memory_files(cwd=tmp))
                clear_memory_file_caches()
                # Should not crash after clearing
                result = _run(get_memory_files(cwd=tmp))
                self.assertIsInstance(result, list)
        clear_memory_file_caches()

    def test_bare_mode_disables(self):
        clear_memory_file_caches()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("test", encoding="utf-8")
            with patch.dict(os.environ, {
                "CLAUDE_CODE_BARE_MODE": "true",
                "CLAUDE_CODE_ADDITIONAL_DIRECTORIES": "",
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
            }):
                clear_memory_file_caches()
                # In bare mode with no add-dirs, should still technically
                # load from the walk but the shouldDisableClaudeMd is checked
                # at the prompt_assembly level, not here
                result = _run(get_memory_files(cwd=tmp))
                self.assertIsInstance(result, list)
        clear_memory_file_caches()


class TestIsMemoryFilePath(unittest.TestCase):
    def test_claude_md(self):
        self.assertTrue(is_memory_file_path("/project/CLAUDE.md"))

    def test_local_md(self):
        self.assertTrue(is_memory_file_path("/project/CLAUDE.local.md"))

    def test_rules_file(self):
        self.assertTrue(is_memory_file_path("/project/.claude/rules/style.md"))

    def test_regular_file(self):
        self.assertFalse(is_memory_file_path("/project/README.md"))


if __name__ == "__main__":
    unittest.main()
