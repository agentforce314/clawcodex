"""Wiki text utils — port of typescript/src/services/wiki/utils.ts.

Pure/mechanical: slug sanitization, a NON-LLM 280-char summary (a truncate,
not a model call), and first-line title extraction.
"""

from __future__ import annotations

import re


def sanitize_wiki_slug(value: str) -> str:
    """Port of ``sanitizeWikiSlug``: lowercase, non-alnum → ``-``, collapse
    and trim dashes."""
    out = value.lower()
    out = re.sub(r"[^a-z0-9]+", "-", out)
    out = re.sub(r"^-+|-+$", "", out)
    out = re.sub(r"-{2,}", "-", out)
    return out


def summarize_text(text: str, max_len: int = 280) -> str:
    """Port of ``summarizeText``: whitespace-normalize; empty → the default;
    ≤max → itself; else truncate to ``max_len-1`` + ``…`` (U+2026, verbatim
    to TS)."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return "No summary available."
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1].rstrip() + "…"


def extract_title_from_text(fallback_name: str, content: str) -> str:
    """Port of ``extractTitleFromText``: the first non-empty trimmed line,
    with a leading ``#…`` stripped; empty → the fallback."""
    first_non_empty = None
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped:
            first_non_empty = stripped
            break
    if not first_non_empty:
        return fallback_name
    return re.sub(r"^#+\s*", "", first_non_empty) or fallback_name
