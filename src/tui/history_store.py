"""Persistent prompt-history store for the Textual TUI.

Mirrors ``getTimestampedHistory`` / ``logToHistory`` used by
``HistorySearchDialog`` on the TypeScript side. Entries live in
``~/.clawcodex/history.jsonl`` (one JSON object per line) so the file
is trivial to tail, grep, or rotate. Each record carries the raw
prompt text plus a Unix timestamp so dialogs can present them in
reverse-chronological order.

The store is intentionally process-local (no shared lock): Textual
runs a single-UI process and the legacy Rich REPL writes to a
different file already. Concurrent writers across multiple TUI
instances may interleave lines but never corrupt individual records,
since each record is a full line that ends in ``\\n``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_DEFAULT_PATH = Path(os.path.expanduser("~/.clawcodex/history.jsonl"))
_MAX_ENTRIES = 1000


@dataclass
class HistoryRecord:
    prompt: str
    timestamp: float


class HistoryStore:
    """Append-only history of user prompts, bounded to ``max_entries``."""

    def __init__(self, path: Path | None = None, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_PATH
        self._max = max_entries

    @property
    def path(self) -> Path:
        return self._path

    # ---- write ----
    def append(self, prompt: str) -> None:
        prompt = (prompt or "").strip()
        if not prompt:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fp:
                json.dump({"prompt": prompt, "ts": time.time()}, fp, ensure_ascii=False)
                fp.write("\n")
            self._maybe_rotate()
        except Exception:
            return

    # ---- read ----
    def load(self) -> list[HistoryRecord]:
        if not self._path.exists():
            return []
        records: list[HistoryRecord] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prompt = str(obj.get("prompt") or "")
                if not prompt:
                    continue
                ts = float(obj.get("ts") or 0.0)
                records.append(HistoryRecord(prompt=prompt, timestamp=ts))
        except Exception:
            return []
        return records

    def recent(self, limit: int | None = None) -> list[HistoryRecord]:
        records = self.load()
        records.reverse()
        if limit is not None:
            records = records[:limit]
        return records

    # ---- housekeeping ----
    def _maybe_rotate(self) -> None:
        try:
            records = self.load()
        except Exception:
            return
        if len(records) <= self._max:
            return
        keep = records[-self._max :]
        try:
            with self._path.open("w", encoding="utf-8") as fp:
                for record in keep:
                    json.dump({"prompt": record.prompt, "ts": record.timestamp}, fp, ensure_ascii=False)
                    fp.write("\n")
        except Exception:
            return


def ingest_many(store: HistoryStore, prompts: Iterable[str]) -> None:
    for prompt in prompts:
        store.append(prompt)


__all__ = ["HistoryRecord", "HistoryStore", "ingest_many"]
