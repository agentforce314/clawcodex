from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# One line terminator at end-of-line (difflib is fed splitlines(keepends=True),
# so hunk lines arrive as a mix of "\n" / "\r\n" / bare-final-line).
_LINE_TERM_RE = re.compile(r"(?:\r\n|\r|\n)$")

_LEADING_TABS_RE = re.compile(r"^\t+", re.MULTILINE)


def convert_leading_tabs_to_spaces(content: str) -> str:
    """Leading tabs → 2 spaces each, for display patches only.

    Mirrors the TS ``convertLeadingTabsToSpaces`` (utils/diff.ts) that the
    original applies to both sides before computing ``structuredPatch`` — a
    raw ``\\t`` reaching the TUI renderer has terminal-dependent width and
    breaks the diff gutter/padding math.
    """
    if "\t" not in content:
        return content
    return _LEADING_TABS_RE.sub(lambda m: "  " * len(m.group(0)), content)


def unified_diff_hunks(diff_lines: Iterable[str]) -> list[dict]:
    """Parse ``difflib.unified_diff`` output into jsdiff StructuredPatchHunk dicts.

    Hunk lines keep their ``+``/``-``/`` `` marker but are stripped of the
    single trailing line terminator so the shape matches jsdiff's
    ``structuredPatch`` (whose lines carry no terminators) — consumers concat
    them with ``\\n`` and index into them for word-diff ranges.
    """
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
            # difflib's ---/+++ file headers precede the first @@, so this
            # guard is what skips them. Inside a hunk every line starts with
            # +/-/space; a removed "-- sql comment" emits "--- sql comment"
            # and MUST be kept (an explicit header skip here used to eat it).
            continue
        current["lines"].append(_LINE_TERM_RE.sub("", line))
    if current is not None:
        hunks.append(current)
    return hunks

