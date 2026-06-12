"""Continuation-nudge detection — port of TS query.ts:1443-1512.

Detects when the model SIGNALS intent to continue ("so now I need to
edit…", "let me run…") but returned no tool calls, so the loop can
inject a nudge instead of ending the turn prematurely. Capped at
``MAX_CONTINUATION_NUDGES`` per turn-chain (query.ts:169) — this cap
belongs HERE, not to the token-budget continuation (a separate
mechanism bounded by its own 90%/diminishing rules).
"""

from __future__ import annotations

import re

MAX_CONTINUATION_NUDGES = 3

NUDGE_MESSAGE = "Continue with the task. Use the appropriate tools to proceed."

# Don't nudge when the model signaled completion (query.ts:1487).
_COMPLETION_MARKERS = re.compile(
    r"\b(done|finished|completed|complete|summary|that's all|that is all"
    r"|all set|hope this helps|let me know if)\b"
)

_ACTION = (
    r"(do|create|write|edit|update|fix|implement|add|run|check|make|build"
    r"|set up)"
)

# Always-on signal patterns (query.ts:1466-1481).
_SIGNALS = [
    re.compile(
        r"\bso now (i|let me|we) (need to|have to|should|must|will) "
        + _ACTION + r"\b"
    ),
    re.compile(
        r"\bnow i('ll| will) "
        r"(do|create|write|edit|update|fix|implement|add|run|check|make"
        r"|build|set up|go|proceed)\b"
    ),
    re.compile(
        r"\blet me (go ahead and |now )?"
        r"(do|create|write|edit|update|fix|implement|add|run|check|make"
        r"|build|set up|proceed)\b"
    ),
    re.compile(
        r"\btime to (do|create|write|edit|update|fix|implement|add|run"
        r"|check|make|build|get started|begin)\b"
    ),
]

# Short-message-only patterns (< 80 chars; query.ts:1472-1475, 1478-1481).
_SHORT_SIGNALS = [
    re.compile(
        r"\b(i('ll| will| need to| have to| must) (now )?" + _ACTION + r")\b"
    ),
    re.compile(
        r"\bnext,?\s+(i('ll| will)|let me|i need to) "
        r"(do|create|write|edit|update|fix|implement|add|run|check|make"
        r"|build)\b"
    ),
]


def detect_continuation_signal(last_text: str) -> bool:
    """True iff the (lowercased) final assistant text signals continuation
    intent without a completion marker."""
    text = last_text.lower()
    if _COMPLETION_MARKERS.search(text):
        return False
    if any(p.search(text) for p in _SIGNALS):
        return True
    if len(text) < 80 and any(p.search(text) for p in _SHORT_SIGNALS):
        return True
    return False
