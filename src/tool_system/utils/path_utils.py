"""Shared path utilities for tools (relative paths, suggestions)."""

from __future__ import annotations

import os
from pathlib import Path


def to_relative_path(absolute: str, cwd: str | Path) -> str:
    """Convert an absolute path to a relative one if shorter."""
    try:
        rel = os.path.relpath(absolute, str(cwd))
    except ValueError:
        return absolute
    if len(rel) < len(absolute):
        return rel
    return absolute


def suggest_path_under_cwd(path: str, cwd: str | Path) -> str | None:
    """Suggest a corrected path if the given path looks like a relative path
    that should be under cwd."""
    name = os.path.basename(path)
    if not name:
        return None
    candidate = os.path.join(str(cwd), name)
    if os.path.exists(candidate):
        return candidate
    return None
