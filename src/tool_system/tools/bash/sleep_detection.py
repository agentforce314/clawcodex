"""Detect long standalone or leading ``sleep N`` patterns to avoid.

Catches ``sleep 6``, ``sleep 6 && check``, ``sleep 6; check`` -- but permits
the 1-5 second waits recommended by the Bash tool prompt.  Sleep inside
pipelines, subshells, or scripts is left alone.
"""

from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||[;])\s*")
_SLEEP_RE = re.compile(r"^sleep\s+(\d+)\s*$")


def detect_blocked_sleep_pattern(command: str) -> str | None:
    """Return a description of the blocked sleep pattern, or ``None`` if OK."""
    parts = _SPLIT_RE.split(command.strip())
    if not parts:
        return None
    first = parts[0].strip()
    m = _SLEEP_RE.match(first)
    if not m:
        return None
    secs = int(m.group(1))
    if secs <= 5:
        return None

    rest = " ".join(p.strip() for p in parts[1:] if p.strip())
    if rest:
        return f"sleep {secs} followed by: {rest}"
    return f"standalone sleep {secs}"
