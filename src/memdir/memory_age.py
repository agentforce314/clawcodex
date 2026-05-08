"""Memory staleness utilities.

Ports `typescript/src/memdir/memoryAge.ts`. Human-readable age strings
("today" / "yesterday" / "N days ago") are intentional — eval-validated
as triggering the model's staleness reasoning where ISO timestamps
don't.
"""

from __future__ import annotations

import math
import time

__all__ = [
    "memory_age_days",
    "memory_age",
    "memory_freshness_text",
    "memory_freshness_note",
]

_MS_PER_DAY = 86_400_000


def memory_age_days(mtime_ms: float) -> int:
    """Days elapsed since *mtime_ms*. Floor-rounded; clamps to ≥0.

    A future *mtime_ms* (clock skew) returns 0, not a negative.
    """
    delta = (time.time() * 1000.0) - mtime_ms
    return max(0, math.floor(delta / _MS_PER_DAY))


def memory_age(mtime_ms: float) -> str:
    """Human-readable age string."""
    days = memory_age_days(mtime_ms)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def memory_freshness_text(mtime_ms: float) -> str:
    """Plain-text staleness caveat for memories >1 day old.

    Returns the empty string for fresh (today/yesterday) memories —
    a warning there is noise. Use this when the consumer already wraps
    its own ``<system-reminder>`` (e.g. relevant-memory injection).
    """
    days = memory_age_days(mtime_ms)
    if days <= 1:
        return ""
    return (
        f"This memory is {days} days old. "
        f"Memories are point-in-time observations, not live state — "
        f"claims about code behavior or file:line citations may be outdated. "
        f"Verify against current code before asserting as fact."
    )


def memory_freshness_note(mtime_ms: float) -> str:
    """Per-memory staleness note wrapped in ``<system-reminder>`` tags.

    Returns ``""`` for memories ≤1 day old. Use for callers that don't
    add their own system-reminder wrapper (e.g. file-read tool output).
    """
    text = memory_freshness_text(mtime_ms)
    if not text:
        return ""
    return f"<system-reminder>{text}</system-reminder>\n"
