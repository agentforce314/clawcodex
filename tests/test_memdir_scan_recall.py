"""Tests for src/memdir/memory_scan.py + find_relevant_memories.py — Slice B."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.memdir.find_relevant_memories import (
    MAX_RELEVANT_MEMORIES,
    RelevantMemory,
    find_relevant_memories,
)
from src.memdir.memory_age import (
    memory_age,
    memory_age_days,
    memory_freshness_note,
    memory_freshness_text,
)
from src.memdir.memory_scan import (
    FRONTMATTER_MAX_LINES,
    MAX_DEPTH,
    MAX_MEMORY_FILES,
    format_memory_manifest,
    scan_memory_files,
)


def _write_memory_file(
    path: Path,
    *,
    name: str = "x",
    description: str = "test memory",
    type_: str | None = "feedback",
    body: str = "body content",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    type_line = f"type: {type_}\n" if type_ else ""
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n{type_line}---\n\n{body}\n",
        encoding="utf-8",
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ScanShallowTest(unittest.TestCase):
    def test_finds_md_at_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "feedback_test.md")
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 1)
            self.assertEqual(headers[0].filename, "feedback_test.md")
            self.assertEqual(headers[0].type, "feedback")
            self.assertEqual(headers[0].description, "test memory")

    def test_excludes_memory_md_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "MEMORY.md").write_text("# index", encoding="utf-8")
            _write_memory_file(Path(tmp) / "user_role.md", type_="user")
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 1)
            self.assertEqual(headers[0].filename, "user_role.md")

    def test_depth_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "shallow.md")
            # Depth 5 — should be excluded
            deep = Path(tmp, "d1", "d2", "d3", "d4", "d5")
            _write_memory_file(deep / "deep.md")
            headers = _run(scan_memory_files(tmp))
            filenames = [h.filename for h in headers]
            self.assertIn("shallow.md", filenames)
            self.assertFalse(any("deep.md" in f for f in filenames))

    def test_unknown_type_falls_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "weird.md", type_="bogus")
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 1)
            self.assertIsNone(headers[0].type)

    def test_no_frontmatter_yields_none_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "raw.md").write_text("# a heading\nno fm\n", encoding="utf-8")
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(len(headers), 1)
            self.assertIsNone(headers[0].description)


class ScanSortAndCapTest(unittest.TestCase):
    def test_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old.md"
            new = Path(tmp) / "new.md"
            _write_memory_file(old)
            time.sleep(0.02)
            _write_memory_file(new)
            # Touch new explicitly to be sure mtime is later
            os.utime(new, (time.time(), time.time()))
            os.utime(old, (time.time() - 100, time.time() - 100))
            headers = _run(scan_memory_files(tmp))
            self.assertEqual(headers[0].filename, "new.md")
            self.assertEqual(headers[1].filename, "old.md")


class ManifestTest(unittest.TestCase):
    def test_manifest_includes_type_tag_and_iso_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "user_role.md", type_="user")
            headers = _run(scan_memory_files(tmp))
            manifest = format_memory_manifest(headers)
            self.assertIn("[user]", manifest)
            self.assertIn("user_role.md", manifest)
            self.assertIn("test memory", manifest)
            # ISO timestamp ends with Z
            self.assertIn("Z", manifest)


class StalenessTest(unittest.TestCase):
    def test_today(self):
        now_ms = time.time() * 1000.0
        self.assertEqual(memory_age_days(now_ms), 0)
        self.assertEqual(memory_age(now_ms), "today")
        self.assertEqual(memory_freshness_text(now_ms), "")

    def test_yesterday(self):
        ms = (time.time() - 86_400) * 1000.0
        self.assertEqual(memory_age(ms), "yesterday")
        self.assertEqual(memory_freshness_text(ms), "")

    def test_47_days_ago(self):
        ms = (time.time() - 47 * 86_400) * 1000.0
        self.assertEqual(memory_age(ms), "47 days ago")
        text = memory_freshness_text(ms)
        self.assertIn("47 days old", text)
        self.assertIn("file:line citations", text)
        # Wrapped form uses system-reminder tags
        note = memory_freshness_note(ms)
        self.assertIn("<system-reminder>", note)
        self.assertIn("</system-reminder>", note)

    def test_future_clamps_to_zero(self):
        future = (time.time() + 86_400) * 1000.0
        self.assertEqual(memory_age_days(future), 0)


class FindRelevantMemoriesTest(unittest.TestCase):
    def test_validates_filenames_against_known_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "a.md", description="alpha")
            _write_memory_file(Path(tmp) / "b.md", description="beta")

            # Mock provider returns a real filename plus a bogus one.
            mock_resp = MagicMock()
            mock_resp.content = '{"selected_memories": ["a.md", "bogus.md"]}'
            mock_provider = MagicMock()
            mock_provider.chat_async = MagicMock(
                return_value=_async_return(mock_resp)
            )

            cancel = asyncio.Event()
            result = _run(
                find_relevant_memories(
                    "give me alpha",
                    tmp,
                    cancel_event=cancel,
                    provider=mock_provider,
                )
            )
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].path.endswith("a.md"))

    def test_provider_error_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "a.md")

            mock_provider = MagicMock()
            mock_provider.chat_async = MagicMock(
                side_effect=RuntimeError("boom")
            )

            cancel = asyncio.Event()
            result = _run(
                find_relevant_memories(
                    "irrelevant",
                    tmp,
                    cancel_event=cancel,
                    provider=mock_provider,
                )
            )
            self.assertEqual(result, [])

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mock_provider = MagicMock()
            cancel = asyncio.Event()
            result = _run(
                find_relevant_memories(
                    "anything",
                    tmp,
                    cancel_event=cancel,
                    provider=mock_provider,
                )
            )
            self.assertEqual(result, [])
            mock_provider.chat_async.assert_not_called()

    def test_already_surfaced_filtered_before_select(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_memory_file(Path(tmp) / "a.md")
            _write_memory_file(Path(tmp) / "b.md")
            a_path = str(Path(tmp) / "a.md")

            mock_resp = MagicMock()
            mock_resp.content = '{"selected_memories": ["a.md", "b.md"]}'
            mock_provider = MagicMock()
            mock_provider.chat_async = MagicMock(
                return_value=_async_return(mock_resp)
            )

            cancel = asyncio.Event()
            result = _run(
                find_relevant_memories(
                    "anything",
                    tmp,
                    cancel_event=cancel,
                    provider=mock_provider,
                    already_surfaced={a_path},
                )
            )
            # a.md was already surfaced and filtered; selector still
            # returned both names but only b.md is in the post-filter
            # known set.
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].path.endswith("b.md"))


def _async_return(value):
    """Return an awaitable that resolves to *value* — for MagicMock."""

    async def _coro():
        return value

    return _coro()


if __name__ == "__main__":
    unittest.main()
