"""Output formatting and truncation utilities for the bash tool."""

from __future__ import annotations

import os

_DEFAULT_MAX_OUTPUT_LENGTH = 30_000

_ENVVAR = "BASH_MAX_OUTPUT_LENGTH"


def get_max_output_length() -> int:
    raw = os.environ.get(_ENVVAR)
    if raw is not None:
        try:
            val = int(raw)
            return max(1000, min(val, 150_000))
        except (ValueError, TypeError):
            pass
    return _DEFAULT_MAX_OUTPUT_LENGTH


def truncate_output(s: str, limit: int | None = None) -> str:
    """Truncate *s* to *limit* characters, reporting truncated line count."""
    if limit is None:
        limit = get_max_output_length()
    if len(s) <= limit:
        return s

    truncated_part = s[:limit]
    remaining = s[limit:]
    remaining_lines = remaining.count("\n") + 1
    return f"{truncated_part}\n\n... [{remaining_lines} lines truncated] ..."


def strip_empty_lines(content: str) -> str:
    """Strip leading/trailing lines that contain only whitespace."""
    lines = content.split("\n")

    start = 0
    while start < len(lines) and lines[start].strip() == "":
        start += 1

    end = len(lines) - 1
    while end >= 0 and lines[end].strip() == "":
        end -= 1

    if start > end:
        return ""

    return "\n".join(lines[start:end + 1])


def strip_leading_blank_lines(s: str) -> str:
    """Remove leading lines that are blank or whitespace-only."""
    import re
    return re.sub(r"^(\s*\n)+", "", s)
