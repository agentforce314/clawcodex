"""Tests for ``src.agent.transcript`` — Chunk C / WI-2.2 (gate-zero).

Covers: path resolution + path-traversal sanitization, JSONL append
semantics, atomic-line concurrent writes, reader tolerance of trailing
partial lines, blank-line skipping, ``read_all`` round-trip, and the
``TranscriptReader`` interface availability for the Phase-7 DIP claim.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from src.agent.transcript import (
    TranscriptReader,
    TranscriptWriter,
    ensure_transcript_dir,
    get_agent_transcript_path,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_transcript_path_uses_agent_id_and_jsonl_suffix() -> None:
    p = get_agent_transcript_path("a1b2c3d4z")
    assert p.endswith("/a1b2c3d4z.jsonl")
    # Stable under .clawcodex/transcripts/
    assert ".clawcodex/transcripts" in p


def test_transcript_path_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="invalid agent_id"):
        get_agent_transcript_path("../../etc/passwd")


def test_transcript_path_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        get_agent_transcript_path("")


def test_transcript_path_rejects_overly_long_id() -> None:
    with pytest.raises(ValueError, match="too long"):
        get_agent_transcript_path("a" * 65)


def test_transcript_path_accepts_alphanumeric_and_dashes() -> None:
    # Should not raise.
    p = get_agent_transcript_path("a-b_c-9z")
    assert p.endswith("a-b_c-9z.jsonl")


def test_ensure_transcript_dir_creates_and_returns_path() -> None:
    root = ensure_transcript_dir()
    assert Path(root).is_dir()


# ---------------------------------------------------------------------------
# Writer — round-trip and atomic appends
# ---------------------------------------------------------------------------


def test_writer_appends_one_jsonl_line_per_call(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append({"role": "user", "content": "hi"})
        w.append({"role": "assistant", "content": "ok"})
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"role": "user", "content": "hi"}
    assert json.loads(lines[1]) == {"role": "assistant", "content": "ok"}


def test_writer_append_is_terminated_with_newline(tmp_path: Path) -> None:
    """Reader iterator depends on every line being terminated."""
    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append({"k": 1})
    assert path.read_bytes().endswith(b"\n")


def test_writer_close_is_idempotent(tmp_path: Path) -> None:
    w = TranscriptWriter(tmp_path / "x.jsonl")
    w.close()
    w.close()  # second call must not raise


def test_writer_after_close_raises(tmp_path: Path) -> None:
    w = TranscriptWriter(tmp_path / "x.jsonl")
    w.close()
    with pytest.raises(RuntimeError, match="closed"):
        w.append({"k": 1})


def test_writer_dataclass_serialization(tmp_path: Path) -> None:
    """The writer should accept dataclasses (Message subclasses are
    dataclasses) via ``asdict``."""
    from dataclasses import dataclass

    @dataclass
    class Stub:
        field: str
        n: int

    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append(Stub(field="hello", n=42))
    parsed = json.loads(path.read_text().strip())
    assert parsed == {"field": "hello", "n": 42}


def test_writer_unserializable_object_does_not_crash(tmp_path: Path) -> None:
    """A non-JSON-able object falls back to ``repr`` rather than
    bringing down the writer mid-run."""
    class WeirdThing:
        def __repr__(self) -> str:
            return "<WeirdThing>"

    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append(WeirdThing())  # type: ignore[arg-type]
    parsed = json.loads(path.read_text().strip())
    assert "_unserializable" in parsed


def test_writer_concurrent_appends_do_not_interleave(tmp_path: Path) -> None:
    """Multiple writers on the same O_APPEND fd are atomic at line
    granularity for sub-PIPE_BUF lines."""
    path = tmp_path / "x.jsonl"
    n_threads = 4
    n_writes = 50

    def worker(idx: int) -> None:
        with TranscriptWriter(path) as w:
            for j in range(n_writes):
                w.append({"thread": idx, "n": j})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every line must be a complete, parseable JSON object.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_writes
    parsed = [json.loads(line) for line in lines]
    # Each (thread, n) pair appears exactly once.
    pairs = {(p["thread"], p["n"]) for p in parsed}
    assert len(pairs) == n_threads * n_writes


# ---------------------------------------------------------------------------
# Reader — tolerance + round-trip
# ---------------------------------------------------------------------------


def test_reader_round_trips_writer_output(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append({"a": 1})
        w.append({"b": 2})
    items = list(TranscriptReader(path))
    assert items == [{"a": 1}, {"b": 2}]


def test_reader_yields_nothing_when_file_missing(tmp_path: Path) -> None:
    path = tmp_path / "absent.jsonl"
    assert list(TranscriptReader(path)) == []


def test_reader_skips_unparseable_trailing_line(tmp_path: Path) -> None:
    """Simulate writer-crashed-mid-write — the last line is partial.
    Reader logs once and skips, returns the parseable prefix."""
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"good": 1}\n{"good": 2}\n{"partial":')  # missing closing
    items = list(TranscriptReader(path))
    assert items == [{"good": 1}, {"good": 2}]


def test_reader_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"a":1}\n\n\n{"b":2}\n', encoding="utf-8")
    assert list(TranscriptReader(path)) == [{"a": 1}, {"b": 2}]


def test_reader_read_all_materializes_list(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append({"x": 1})
        w.append({"x": 2})
        w.append({"x": 3})
    assert TranscriptReader(path).read_all() == [{"x": 1}, {"x": 2}, {"x": 3}]


def test_reader_logs_partial_line_only_once(tmp_path: Path, caplog) -> None:
    """A corrupt transcript should not spam the log with one warning
    per line — the reader logs at most once per instance."""
    path = tmp_path / "x.jsonl"
    # Two consecutive unparseable lines.
    path.write_bytes(b"not-json-1\nnot-json-2\n")
    with caplog.at_level("WARNING"):
        list(TranscriptReader(path))
    warnings = [r for r in caplog.records if "unparseable transcript" in r.message]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Filesystem permissions — transcripts can hold sensitive prompts
# ---------------------------------------------------------------------------


def test_writer_creates_file_with_user_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with TranscriptWriter(path) as w:
        w.append({"k": 1})
    mode = os.stat(path).st_mode & 0o777
    # File should be 0o600 (owner read+write only). umask may strip
    # group/world bits we asked for; we only require they ARE stripped.
    assert mode & 0o077 == 0, f"transcript leaks permissions: {oct(mode)}"
