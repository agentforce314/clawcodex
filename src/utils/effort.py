"""Effort level system matching TypeScript utils/effort.ts."""

from __future__ import annotations

from enum import Enum
from typing import Any


class EffortLevel(Enum):
    """Effort levels that control thinking depth and token budgets."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


# Keyword → effort level mapping for user input detection
EFFORT_KEYWORDS: dict[str, EffortLevel] = {
    "low": EffortLevel.LOW,
    "quick": EffortLevel.LOW,
    "fast": EffortLevel.LOW,
    "brief": EffortLevel.LOW,
    "medium": EffortLevel.MEDIUM,
    "normal": EffortLevel.MEDIUM,
    "high": EffortLevel.HIGH,
    "thorough": EffortLevel.HIGH,
    "detailed": EffortLevel.HIGH,
    "max": EffortLevel.MAX,
    "maximum": EffortLevel.MAX,
    "comprehensive": EffortLevel.MAX,
}

# Max output tokens per effort level
_MAX_TOKENS_FOR_EFFORT: dict[EffortLevel, int] = {
    EffortLevel.LOW: 4_096,
    EffortLevel.MEDIUM: 8_192,
    EffortLevel.HIGH: 16_384,
    EffortLevel.MAX: 32_768,
}


def resolve_applied_effort(
    *,
    user_effort: str | None = None,
    config_effort: str | None = None,
    model_default: str | None = None,
) -> EffortLevel:
    """Resolve the applied effort level from multiple sources.

    Priority: user_effort > config_effort > model_default > MEDIUM.
    """
    for source in (user_effort, config_effort, model_default):
        if source:
            source_lower = source.lower().strip()
            # Direct enum value
            try:
                return EffortLevel(source_lower)
            except ValueError:
                pass
            # Keyword lookup
            if source_lower in EFFORT_KEYWORDS:
                return EFFORT_KEYWORDS[source_lower]

    return EffortLevel.MEDIUM


def get_max_tokens_for_effort(effort: EffortLevel) -> int:
    """Get the maximum output tokens for a given effort level."""
    return _MAX_TOKENS_FOR_EFFORT.get(effort, 8_192)


def detect_effort_from_text(text: str) -> EffortLevel | None:
    """Detect effort level from user text (e.g. '--effort high', '--thorough').

    Returns None if no effort indicator found.
    """
    lower = text.lower()

    # Check --effort flag
    if "--effort" in lower:
        parts = lower.split("--effort")
        if len(parts) > 1:
            word = parts[1].strip().split()[0] if parts[1].strip() else ""
            if word in EFFORT_KEYWORDS:
                return EFFORT_KEYWORDS[word]
            try:
                return EffortLevel(word)
            except ValueError:
                pass

    # Check --thorough, --quick flags
    if "--thorough" in lower:
        return EffortLevel.HIGH
    if "--quick" in lower or "--fast" in lower:
        return EffortLevel.LOW
    if "--max" in lower:
        return EffortLevel.MAX

    return None
