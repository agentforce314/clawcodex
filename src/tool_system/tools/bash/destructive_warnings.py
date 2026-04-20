"""Detects potentially destructive bash commands and returns a warning string.

Purely informational -- does not affect permission logic or auto-approval.
"""

from __future__ import annotations

import re

_DestructivePattern = tuple[re.Pattern[str], str]

_DESTRUCTIVE_PATTERNS: list[_DestructivePattern] = [
    # Git -- data loss / hard to reverse
    (re.compile(r"\bgit\s+reset\s+--hard\b"),
     "Note: may discard uncommitted changes"),
    (re.compile(r"\bgit\s+push\b[^;&|\n]*[ \t](--force|--force-with-lease|-f)\b"),
     "Note: may overwrite remote history"),
    (re.compile(r"\bgit\s+clean\b(?![^;&|\n]*(?:-[a-zA-Z]*n|--dry-run))[^;&|\n]*-[a-zA-Z]*f"),
     "Note: may permanently delete untracked files"),
    (re.compile(r"\bgit\s+checkout\s+(--\s+)?\.[ \t]*($|[;&|\n])"),
     "Note: may discard all working tree changes"),
    (re.compile(r"\bgit\s+restore\s+(--\s+)?\.[ \t]*($|[;&|\n])"),
     "Note: may discard all working tree changes"),
    (re.compile(r"\bgit\s+stash[ \t]+(drop|clear)\b"),
     "Note: may permanently remove stashed changes"),
    (re.compile(r"\bgit\s+branch\s+(-D[ \t]|--delete\s+--force|--force\s+--delete)\b"),
     "Note: may force-delete a branch"),
    # Git -- safety bypass
    (re.compile(r"\bgit\s+(commit|push|merge)\b[^;&|\n]*--no-verify\b"),
     "Note: may skip safety hooks"),
    (re.compile(r"\bgit\s+commit\b[^;&|\n]*--amend\b"),
     "Note: may rewrite the last commit"),
    # File deletion
    (re.compile(r"(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR][a-zA-Z]*f|(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]"),
     "Note: may recursively force-remove files"),
    (re.compile(r"(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR]"),
     "Note: may recursively remove files"),
    (re.compile(r"(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*f"),
     "Note: may force-remove files"),
    # Database
    (re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
     "Note: may drop or truncate database objects"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+[ \t]*(;|\"|'|\n|$)", re.IGNORECASE),
     "Note: may delete all rows from a database table"),
    # Infrastructure
    (re.compile(r"\bkubectl\s+delete\b"),
     "Note: may delete Kubernetes resources"),
    (re.compile(r"\bterraform\s+destroy\b"),
     "Note: may destroy Terraform infrastructure"),
]


def get_destructive_command_warning(command: str) -> str | None:
    """Return a human-readable warning if *command* matches a destructive pattern."""
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return warning
    return None
