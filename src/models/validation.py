"""Model name validation matching TypeScript model/validateModel.ts."""

from __future__ import annotations

import re

from .aliases import MODEL_ALIASES
from .configs import MODEL_CONFIGS

# Pattern for valid Claude model IDs
_CLAUDE_PATTERN = re.compile(
    r"^claude-(?:sonnet|opus|haiku|3|3-5|3-7)(?:-\d+)?(?:-\d{8})?$"
)


def validate_model_name(name: str) -> bool:
    """Check if a model name is valid (known model, alias, or valid format)."""
    if not name:
        return False

    # Known model
    if name in MODEL_CONFIGS:
        return True

    # Known alias
    if name.lower() in MODEL_ALIASES:
        return True

    # Valid Claude pattern
    if _CLAUDE_PATTERN.match(name):
        return True

    # Allow any non-empty string for third-party models (OpenAI, etc.)
    return len(name) >= 2


def is_model_allowed(
    model_id: str,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
) -> bool:
    """Check if a model is allowed by allowlist/denylist rules."""
    if denylist:
        for pattern in denylist:
            if _matches_pattern(model_id, pattern):
                return False

    if allowlist:
        for pattern in allowlist:
            if _matches_pattern(model_id, pattern):
                return True
        return False

    return True


def _matches_pattern(model_id: str, pattern: str) -> bool:
    """Check if model_id matches a pattern (supports * wildcard)."""
    if pattern == "*":
        return True
    if "*" in pattern:
        regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
        return bool(re.match(regex, model_id))
    return model_id == pattern
