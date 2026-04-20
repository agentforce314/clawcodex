"""Model alias table matching TypeScript model/aliases.ts."""

from __future__ import annotations

MODEL_ALIASES: dict[str, str] = {
    # Short names → canonical
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-3-5-haiku-20241022",

    # Version aliases
    "claude-4-sonnet": "claude-sonnet-4-20250514",
    "claude-4-opus": "claude-opus-4-20250514",
    "claude-sonnet": "claude-sonnet-4-20250514",
    "claude-opus": "claude-opus-4-20250514",
    "claude-haiku": "claude-3-5-haiku-20241022",

    # Legacy aliases
    "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3.5-haiku": "claude-3-5-haiku-20241022",
    "claude-3-sonnet": "claude-3-sonnet-20240229",
    "claude-3-opus": "claude-3-opus-20240229",
    "claude-3-haiku": "claude-3-haiku-20240307",
    "claude-3.7-sonnet": "claude-3-7-sonnet-20250219",

    # Common typos / shortcuts
    "s4": "claude-sonnet-4-20250514",
    "o4": "claude-opus-4-20250514",
    "h35": "claude-3-5-haiku-20241022",
}


def resolve_alias(name: str) -> str:
    """Resolve a model alias to its canonical name.

    Returns the input unchanged if not an alias.
    """
    return MODEL_ALIASES.get(name.lower(), name)
