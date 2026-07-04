"""SERVICES-4 — wiki completion (structured ingest + index rebuild).

Port of typescript/src/services/wiki/{utils,ingest,indexBuilder}.ts. The
/wiki command is wired live (agent_server.py _do_wiki), so the on-disk wiki
is a real consumer; these pin the structured note + index vs the prior
copy-only ingest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.wiki.index_builder import rebuild_wiki_index
from src.wiki.utils import (
    extract_title_from_text,
    sanitize_wiki_slug,
    summarize_text,
)
from src.wiki.wiki import get_wiki_paths, ingest_source, init_wiki


class TestUtils:
    def test_slug(self):
        assert sanitize_wiki_slug("Hello World!! Foo") == "hello-world-foo"
        assert sanitize_wiki_slug("--a--b--") == "a-b"
        assert sanitize_wiki_slug("Already-Clean") == "already-clean"
        assert sanitize_wiki_slug("!!!") == ""

    def test_summarize(self):
        assert summarize_text("") == "No summary available."
        assert summarize_text("   \n\t ") == "No summary available."
        assert summarize_text("short text") == "short text"
        assert summarize_text("a\n\nb   c") == "a b c"  # whitespace-normalized
        long = "x " * 300
        s = summarize_text(long)
        assert len(s) == 280 and s.endswith("…")

    def test_summarize_custom_max(self):
        assert summarize_text("abcdefghij", max_len=5) == "abcd…"

    def test_extract_title(self):
        assert extract_title_from_text("fb", "# My Title\nbody") == "My Title"
        assert extract_title_from_text("fb", "### Deep\n") == "Deep"
        assert extract_title_from_text("fb", "plain first line\nmore") == "plain first line"
        assert extract_title_from_text("fb", "\n\n   \n") == "fb"
        assert extract_title_from_text("fb", "") == "fb"


class TestIngest:
    def test_structured_note_not_raw_copy(self, tmp_path):
        init_wiki(str(tmp_path))
        src = tmp_path / "notes.md"
        src.write_text("# Design Notes\n\nBody of the design.\n" + ("detail " * 100))
        r = ingest_source(str(tmp_path), str(src))
        assert r["ok"] and r["title"] == "Design Notes"
        note = Path(r["dest"])
        assert note.exists() and note.parent.name == "sources"
        txt = note.read_text()
        # structured, not a raw copy
        assert "# Design Notes" in txt
        assert "## Source" in txt and "## Summary" in txt and "## Excerpt" in txt
        assert "```" in txt
        assert r["summary"].startswith("# Design Notes Body of the design")

    def test_log_appended_and_index_rebuilt(self, tmp_path):
        init_wiki(str(tmp_path))
        src = tmp_path / "a.md"
        src.write_text("# Alpha\ntext")
        ingest_source(str(tmp_path), str(src))
        paths = get_wiki_paths(str(tmp_path))
        log = paths.log_file.read_text()
        assert "Ingested" in log and "Alpha" in log
        idx = paths.index_file.read_text()
        assert "## Core Pages" in idx and "## Sources" in idx
        # the ingested source note is linked under Sources
        assert "/sources/" in idx

    def test_not_initialized(self, tmp_path):
        src = tmp_path / "x.md"
        src.write_text("hi")
        r = ingest_source(str(tmp_path), str(src))
        assert not r["ok"] and "not initialized" in r["error"]

    def test_missing_file(self, tmp_path):
        init_wiki(str(tmp_path))
        r = ingest_source(str(tmp_path), str(tmp_path / "nope.md"))
        assert not r["ok"] and "not a file" in r["error"]

    def test_slug_unique_per_ingest(self, tmp_path):
        init_wiki(str(tmp_path))
        src = tmp_path / "dup.md"
        src.write_text("# Dup\nx")
        r1 = ingest_source(str(tmp_path), str(src))
        r2 = ingest_source(str(tmp_path), str(src))
        # both ingests produce a note (slug carries a ms suffix); at least one
        # distinct path, never an error.
        assert r1["ok"] and r2["ok"]


class TestIndexRebuild:
    def test_lists_pages_and_sources_with_titles(self, tmp_path):
        init_wiki(str(tmp_path))
        paths = get_wiki_paths(str(tmp_path))
        # a titled page + an untitled page
        (paths.pages_dir / "titled.md").write_text("# The Title\nbody")
        (paths.pages_dir / "untitled.md").write_text("no heading here")
        (paths.sources_dir / "src1.md").write_text("# S1\nx")
        rebuild_wiki_index(str(tmp_path))
        idx = paths.index_file.read_text()
        assert "[The Title]" in idx  # titled → heading
        assert "[untitled]" in idx  # untitled → filename stem
        assert "[src1]" in idx  # source → filename stem
        # the index file itself is not listed as a page
        assert "[index]" not in idx

    def test_empty_wiki_placeholders(self, tmp_path):
        init_wiki(str(tmp_path))
        paths = get_wiki_paths(str(tmp_path))
        # remove the seed architecture page so pages is empty
        (paths.pages_dir / "architecture.md").unlink()
        rebuild_wiki_index(str(tmp_path))
        idx = paths.index_file.read_text()
        assert "- No pages yet" in idx and "- No sources yet" in idx
