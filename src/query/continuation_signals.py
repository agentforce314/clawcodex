"""Continuation-nudge detection — Ch5/E.4.

Ported from TypeScript query.ts:1444-1505. When the model finishes
without tool calls but signals intent to keep working (e.g., "Let me
now create the file"), inject a polite nudge to keep working. Capped
at MAX_CONTINUATION_NUDGES per turn to prevent infinite loops when
the model keeps matching continuation signals without taking action.
"""
from __future__ import annotations

import re

MAX_CONTINUATION_NUDGES = 3

NUDGE_MESSAGE = (
    "Continue with the task. Use the appropriate tools to proceed."
)

# Action verbs that distinguish "I'll do X" from "I'll explain X".
_ACTIONS = (
    r"do|create|write|edit|update|fix|implement|add|run|check|"
    r"make|build|set up"
)

# Always-on patterns (match regardless of message length).
SIGNALS_ANY_LENGTH = [
    re.compile(
        rf"\bso now (i|let me|we) (need to|have to|should|must|will) "
        rf"({_ACTIONS})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bnow i('ll| will) ({_ACTIONS}|go|proceed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\blet me (go ahead and |now )?({_ACTIONS}|proceed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\btime to ({_ACTIONS}|get started|begin)\b",
        re.IGNORECASE,
    ),
]

# Short-message-only patterns (apply only when len(text) < 80) to
# avoid false positives in long explanatory text.
SIGNALS_SHORT_ONLY = [
    re.compile(
        rf"\b(i('ll| will| need to| have to| must) (now )?({_ACTIONS}))\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bnext,?\s+(i('ll| will)|let me|i need to) ({_ACTIONS})\b",
        re.IGNORECASE,
    ),
]

COMPLETION_MARKERS = re.compile(
    r"\b(done|finished|completed|complete|summary|that's all|"
    r"that is all|all set|hope this helps|let me know if)\b",
    re.IGNORECASE,
)

SHORT_MESSAGE_THRESHOLD = 80


def matches_continuation_signal(text: str) -> bool:
    """Return True if ``text`` signals intent to continue working AND
    does NOT contain completion markers.

    The check has three rules:
      1. Completion markers ("done", "hope this helps", ...) suppress
         the nudge entirely — the model said it's done, take it at
         its word.
      2. "Any length" patterns ("so now I'll create...", "Let me now
         proceed") match regardless of message length.
      3. "Short only" patterns ("I'll fix...", "Next, I'll update...")
         match only in short messages (< 80 chars) to avoid hitting
         them in long explanatory text.
    """
    if COMPLETION_MARKERS.search(text):
        return False
    if any(p.search(text) for p in SIGNALS_ANY_LENGTH):
        return True
    if len(text) < SHORT_MESSAGE_THRESHOLD:
        if any(p.search(text) for p in SIGNALS_SHORT_ONLY):
            return True
    return False
