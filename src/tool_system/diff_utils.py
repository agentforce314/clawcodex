from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def unified_diff_hunks(diff_lines: Iterable[str]) -> list[dict]:
    hunks: list[dict] = []
    current: dict | None = None
    for line in diff_lines:
        m = _HUNK_RE.match(line)
        if m:
            if current is not None:
                hunks.append(current)
            old_start = int(m.group(1))
            old_lines = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_lines = int(m.group(4) or "1")
            current = {
                "oldStart": old_start,
                "oldLines": old_lines,
                "newStart": new_start,
                "newLines": new_lines,
                "lines": [],
            }
            continue
        if current is None:
            continue
        if line.startswith("---") or line.startswith("+++") or line.startswith("\\ No newline"):
            continue
        current["lines"].append(line)
    if current is not None:
        hunks.append(current)
    return hunks

