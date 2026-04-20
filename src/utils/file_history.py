from __future__ import annotations

import difflib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FileSnapshot:
    path: str
    content: str
    timestamp: float = field(default_factory=time.time)
    checkpoint: str | None = None


@dataclass
class LinesChanged:
    added: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        return self.added + self.removed


class FileHistory:
    def __init__(self) -> None:
        self._snapshots: dict[str, list[FileSnapshot]] = {}
        self._generated_files: set[str] = set()
        self._checkpoints: dict[str, float] = {}

    def snapshot_file(
        self,
        path: str,
        content: str | None = None,
        *,
        checkpoint: str | None = None,
    ) -> FileSnapshot:
        abs_path = os.path.abspath(path)

        if content is None:
            try:
                content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except (OSError, IOError):
                content = ""

        snapshot = FileSnapshot(
            path=abs_path,
            content=content,
            checkpoint=checkpoint,
        )

        if abs_path not in self._snapshots:
            self._snapshots[abs_path] = []
        self._snapshots[abs_path].append(snapshot)

        return snapshot

    def undo_file_change(self, path: str) -> str | None:
        abs_path = os.path.abspath(path)
        snapshots = self._snapshots.get(abs_path, [])
        if not snapshots:
            return None

        snapshot = snapshots[-1]
        try:
            Path(abs_path).write_text(snapshot.content, encoding="utf-8")
        except (OSError, IOError) as e:
            logger.error("Failed to restore %s: %s", abs_path, e)
            return None

        snapshots.pop()
        if not snapshots:
            del self._snapshots[abs_path]

        return snapshot.content

    def create_checkpoint(self, name: str) -> str:
        self._checkpoints[name] = time.time()
        for abs_path in list(self._snapshots.keys()):
            try:
                current = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except (OSError, IOError):
                current = ""
            self.snapshot_file(abs_path, current, checkpoint=name)
        return name

    def undo_to_checkpoint(self, name: str) -> list[str]:
        checkpoint_time = self._checkpoints.get(name)
        if checkpoint_time is None:
            return []

        restored: list[str] = []
        for abs_path, snapshots in list(self._snapshots.items()):
            target_snapshot: FileSnapshot | None = None
            for snap in snapshots:
                if snap.checkpoint == name:
                    target_snapshot = snap
                    break

            if target_snapshot is not None:
                try:
                    Path(abs_path).write_text(target_snapshot.content, encoding="utf-8")
                    restored.append(abs_path)
                except (OSError, IOError) as e:
                    logger.error("Failed to restore %s to checkpoint %s: %s", abs_path, name, e)

                idx = snapshots.index(target_snapshot)
                self._snapshots[abs_path] = snapshots[:idx + 1]

        return restored

    def mark_generated(self, path: str) -> None:
        abs_path = os.path.abspath(path)
        self._generated_files.add(abs_path)

    def is_generated(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        return abs_path in self._generated_files

    def get_lines_changed(self, path: str) -> LinesChanged:
        abs_path = os.path.abspath(path)
        snapshots = self._snapshots.get(abs_path, [])
        if not snapshots:
            return LinesChanged()

        original = snapshots[0].content
        try:
            current = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return LinesChanged()

        return _compute_lines_changed(original, current)

    def get_all_lines_changed(self) -> dict[str, LinesChanged]:
        result: dict[str, LinesChanged] = {}
        for abs_path in self._snapshots:
            result[abs_path] = self.get_lines_changed(abs_path)
        return result

    def get_total_lines_changed(self) -> LinesChanged:
        total = LinesChanged()
        for lc in self.get_all_lines_changed().values():
            total.added += lc.added
            total.removed += lc.removed
        return total

    def get_modified_files(self) -> list[str]:
        return list(self._snapshots.keys())

    def get_generated_files(self) -> list[str]:
        return sorted(self._generated_files)

    def get_snapshot_count(self, path: str) -> int:
        abs_path = os.path.abspath(path)
        return len(self._snapshots.get(abs_path, []))

    def clear(self) -> None:
        self._snapshots.clear()
        self._generated_files.clear()
        self._checkpoints.clear()

    @property
    def file_count(self) -> int:
        return len(self._snapshots)

    @property
    def checkpoint_names(self) -> list[str]:
        return list(self._checkpoints.keys())


def _compute_lines_changed(original: str, current: str) -> LinesChanged:
    orig_lines = original.splitlines(keepends=True)
    curr_lines = current.splitlines(keepends=True)

    added = 0
    removed = 0

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, orig_lines, curr_lines
    ).get_opcodes():
        if tag == "replace":
            removed += i2 - i1
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1

    return LinesChanged(added=added, removed=removed)
