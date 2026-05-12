"""Continuation-nudge detection.

Ported verbatim from TypeScript ``query.ts:1444-1505``. When the model
finishes without tool calls but signals intent to continue (e.g.,
"Let me now create the file"), the loop injects a polite nudge to
keep working. Capped at ``MAX_CONTINUATION_NUDGES`` per turn.

The regex set is deliberately tight: require explicit action verbs
and exclude common explanatory phrasing to reduce false positives.
Short-message-only patterns guard against false positives in
explanatory prose.
"""
from __future__ import annotations

import re

MAX_CONTINUATION_NUDGES = 3

# Action verbs that distinguish "I'll do X" from "I'll explain X".
_ACTIONS = (
    "do|create|write|edit|update|fix|implement|add|run|check|"
    "make|build|set up"
)

# Always-on patterns (regardless of message length).
SIGNALS_ANY_LENGTH = [
    re.compile(
        rf"\bso now (i|let me|we) (need to|have to|should|must|will) "
        rf"({_ACTIONS})\b"
    ),
    re.compile(
        rf"\bnow i('ll| will) ({_ACTIONS}|go|proceed)\b"
    ),
    re.compile(
        rf"\blet me (go ahead and |now )?({_ACTIONS}|proceed)\b"
    ),
    re.compile(
        rf"\btime to ({_ACTIONS}|get started|begin)\b"
    ),
]

# Short-message-only patterns (avoid false positives in
# explanatory text). Apply only when len(text) < 80.
SIGNALS_SHORT_ONLY = [
    re.compile(
        rf"\b(i('ll| will| need to| have to| must) (now )?({_ACTIONS}))\b"
    ),
    re.compile(
        rf"\bnext,?\s+(i('ll| will)|let me|i need to) ({_ACTIONS})\b"
    ),
]

COMPLETION_MARKERS = re.compile(
    r"\b(done|finished|completed|complete|summary|that's all|"
    r"that is all|all set|hope this helps|let me know if)\b"
)

NUDGE_MESSAGE = "Continue with the task. Use the appropriate tools to proceed."


def matches_continuation_signal(text: str) -> bool:
    """Returns True if the text signals intent to continue without
    tool calls, AND does not contain completion markers.

    The text should already be lowercased by the caller.
    """
    if COMPLETION_MARKERS.search(text):
        return False
    if any(p.search(text) for p in SIGNALS_ANY_LENGTH):
        return True
    if len(text) < 80:
        if any(p.search(text) for p in SIGNALS_SHORT_ONLY):
            return True
    return False
