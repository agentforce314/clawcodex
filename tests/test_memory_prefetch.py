"""Tests for src/context_system/memory_prefetch.py — WS-5 memory pre-fetch."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.context_system.memory_prefetch import (
    MemoryHeader,
    RelevantMemory,
    _parse_memory_header,
    _select_with_heuristic,
    format_memory_manifest,
    scan_memory_files,
    find_relevant_memories,
)


def _run(coro):
    return asyncio.run(coro)


class TestParseMemoryHeader(unittest.TestCase):
    def test_basic_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notes.md"
            f.write_text("# My Notes\nSome content.", encoding="utf-8")
            header = _parse_memory_header(str(f))
            self.assertIsNotNone(header)
            self.assertEqual(header.filename, "notes.md")
            self.assertEqual(header.description, "My Notes")

    def test_frontmatter_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "mem.md"
            f.write_text("---\ndescription: My memory desc\n---\nContent", encoding="utf-8")
            header = _parse_memory_header(str(f))
            self.assertIsNotNone(header)
            self.assertEqual(header.description, "My memory desc")

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "empty.md"
            f.write_text("", encoding="utf-8")
            header = _parse_memory_header(str(f))
            self.assertIsNone(header)

    def test_nonexistent_file(self):
        header = _parse_memory_header("/nonexistent/file.md")
        self.assertIsNone(header)


class TestScanMemoryFiles(unittest.TestCase):
    def test_basic_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "note1.md").write_text("# Note 1", encoding="utf-8")
            (Path(tmp) / "note2.md").write_text("# Note 2", encoding="utf-8")
            (Path(tmp) / "data.json").write_text("{}", encoding="utf-8")
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 2)
            names = {h.filename for h in headers}
            self.assertIn("note1.md", names)
            self.assertIn("note2.md", names)

    def test_skips_memory_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "MEMORY.md").write_text("# Memory", encoding="utf-8")
            (Path(tmp) / "other.md").write_text("# Other", encoding="utf-8")
            headers = _run(scan_memory_files(tmp))
            names = {h.filename for h in headers}
            self.assertNotIn("MEMORY.md", names)
            self.assertIn("other.md", names)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 0)

    def test_nonexistent_directory(self):
        headers = _run(scan_memory_files("/nonexistent/dir"))
        self.assertEqual(len(headers), 0)


class TestFormatMemoryManifest(unittest.TestCase):
    def test_basic(self):
        headers = [
            MemoryHeader("a.md", "/p/a.md", "Alpha notes", 1000),
            MemoryHeader("b.md", "/p/b.md", "Beta notes", 2000),
        ]
        result = format_memory_manifest(headers)
        self.assertIn("a.md: Alpha notes", result)
        self.assertIn("b.md: Beta notes", result)


class TestSelectWithHeuristic(unittest.TestCase):
    def test_basic_matching(self):
        headers = [
            MemoryHeader("testing.md", "/p/testing.md", "Testing guidelines and practices", 1000),
            MemoryHeader("deploy.md", "/p/deploy.md", "Deployment procedures", 2000),
            MemoryHeader("style.md", "/p/style.md", "Code style rules", 3000),
        ]
        # Use a query with words that match header descriptions (>= 3 chars)
        result = _select_with_heuristic("What are the testing guidelines?", headers)
        self.assertTrue(len(result) > 0)
        # "testing" and "guidelines" should match
        paths = {r.path for r in result}
        self.assertIn("/p/testing.md", paths)

    def test_no_match(self):
        headers = [
            MemoryHeader("deploy.md", "/p/deploy.md", "Deployment procedures", 1000),
        ]
        result = _select_with_heuristic("unrelated query xyz", headers)
        self.assertEqual(len(result), 0)

    def test_empty_headers(self):
        result = _select_with_heuristic("any query", [])
        self.assertEqual(len(result), 0)


class TestFindRelevantMemories(unittest.TestCase):
    def test_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "testing.md").write_text("# Testing\nHow to write tests.", encoding="utf-8")
            (Path(tmp) / "deploy.md").write_text("# Deploy\nHow to deploy.", encoding="utf-8")
            result = _run(find_relevant_memories("testing", tmp))
            self.assertIsInstance(result, list)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(find_relevant_memories("anything", tmp))
            self.assertEqual(len(result), 0)

    def test_already_surfaced_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "testing.md"
            f.write_text("# Testing\nHow to test.", encoding="utf-8")
            result = _run(find_relevant_memories(
                "testing", tmp, already_surfaced={str(f)},
            ))
            paths = {r.path for r in result}
            self.assertNotIn(str(f), paths)


if __name__ == "__main__":
    unittest.main()
