"""Unit tests for :class:`HistoryStore`."""

from __future__ import annotations

import pytest

from src.tui.history_store import HistoryStore


def test_append_and_reload_returns_records(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append("git status")
    store.append("rm -rf build")
    records = store.load()
    assert [r.prompt for r in records] == ["git status", "rm -rf build"]
    assert all(r.timestamp > 0 for r in records)


def test_empty_prompts_are_skipped(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append("")
    store.append("   ")
    assert store.load() == []


def test_recent_is_reverse_chronological(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append("first")
    store.append("second")
    store.append("third")
    assert [r.prompt for r in store.recent()] == ["third", "second", "first"]


def test_recent_limit(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    for i in range(5):
        store.append(f"entry {i}")
    assert [r.prompt for r in store.recent(limit=2)] == ["entry 4", "entry 3"]


def test_rotation_caps_entries(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl", max_entries=3)
    for i in range(10):
        store.append(f"entry {i}")
    records = store.load()
    assert [r.prompt for r in records] == ["entry 7", "entry 8", "entry 9"]


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        '{"prompt": "ok", "ts": 1}\n'
        "this is garbage\n"
        '{"prompt": "ok2", "ts": 2}\n',
        encoding="utf-8",
    )
    store = HistoryStore(path)
    assert [r.prompt for r in store.load()] == ["ok", "ok2"]
