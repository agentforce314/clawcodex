"""Classify bash commands as search, read, list, or silent.

Handles compound commands (&&, ||, ;, |), semantic-neutral commands
(echo, printf, true, false, :), and redirect target skipping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT_RE = re.compile(
    r"(>>|>&|>|&&|\|\||[|;])"
)

SEARCH_COMMANDS: frozenset[str] = frozenset([
    "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
])

READ_COMMANDS: frozenset[str] = frozenset([
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings",
    "jq", "awk", "cut", "sort", "uniq", "tr",
])

LIST_COMMANDS: frozenset[str] = frozenset([
    "ls", "tree", "du",
])

SEMANTIC_NEUTRAL_COMMANDS: frozenset[str] = frozenset([
    "echo", "printf", "true", "false", ":",
])

SILENT_COMMANDS: frozenset[str] = frozenset([
    "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown", "chgrp",
    "touch", "ln", "cd", "export", "unset", "wait",
])


@dataclass(frozen=True)
class SearchOrReadResult:
    is_search: bool = False
    is_read: bool = False
    is_list: bool = False


def _split_with_operators(command: str) -> list[str]:
    """Split command into parts and operators, preserving operator tokens."""
    return [p for p in _SPLIT_RE.split(command) if p.strip()]


def is_search_or_read_command(command: str) -> SearchOrReadResult:
    """Classify a bash command for UI collapsing.

    For pipelines, ALL non-neutral parts must be search/read/list commands
    for the whole command to be considered collapsible.
    """
    try:
        parts = _split_with_operators(command)
    except Exception:
        return SearchOrReadResult()

    if not parts:
        return SearchOrReadResult()

    has_search = False
    has_read = False
    has_list = False
    has_non_neutral = False
    skip_next = False

    for part in parts:
        if skip_next:
            skip_next = False
            continue

        stripped = part.strip()

        if stripped in (">", ">>", ">&"):
            skip_next = True
            continue
        if stripped in ("||", "&&", "|", ";"):
            continue

        base = stripped.split()[0] if stripped.split() else ""
        if not base:
            continue

        if base in SEMANTIC_NEUTRAL_COMMANDS:
            continue

        has_non_neutral = True

        is_search = base in SEARCH_COMMANDS
        is_read = base in READ_COMMANDS
        is_list = base in LIST_COMMANDS

        if not is_search and not is_read and not is_list:
            return SearchOrReadResult()

        if is_search:
            has_search = True
        if is_read:
            has_read = True
        if is_list:
            has_list = True

    if not has_non_neutral:
        return SearchOrReadResult()

    return SearchOrReadResult(
        is_search=has_search,
        is_read=has_read,
        is_list=has_list,
    )


def is_silent_command(command: str) -> bool:
    """Return True when *command* is expected to produce no stdout on success.

    Used to show "Done" instead of "(No output)" in the UI.
    """
    try:
        parts = _split_with_operators(command)
    except Exception:
        return False

    if not parts:
        return False

    has_non_fallback = False
    last_operator: str | None = None
    skip_next = False

    for part in parts:
        if skip_next:
            skip_next = False
            continue

        stripped = part.strip()

        if stripped in (">", ">>", ">&"):
            skip_next = True
            continue
        if stripped in ("||", "&&", "|", ";"):
            last_operator = stripped
            continue

        base = stripped.split()[0] if stripped.split() else ""
        if not base:
            continue

        if last_operator == "||" and base in SEMANTIC_NEUTRAL_COMMANDS:
            continue

        has_non_fallback = True
        if base not in SILENT_COMMANDS:
            return False

    return has_non_fallback
