"""Project-tier settings loader: ``<workspace>/.claude/settings.json``.

Walked up from the workspace root (or cwd) to support multi-workspace
layouts — matches the TS pattern of finding the *closest* ``.claude/``
directory walking towards the filesystem root.

The walk stops at the user's home directory (so we don't pick up
``~/.claude/settings.json`` again — that's the user-tier loader's job).
"""

from __future__ import annotations

from pathlib import Path

from ..hook_types import HookConfig, HookSource
from ._common import parse_hooks_file


def find_project_settings_path(start: Path) -> Path | None:
    """Walk up from ``start`` looking for ``.claude/settings.json``.

    Stops at the user's home directory or the filesystem root, whichever
    comes first. Returns the path if found, else None.
    """
    home = Path.home().resolve()
    cur = start.resolve()
    while True:
        candidate = cur / ".claude" / "settings.json"
        if candidate.exists() and cur != home:
            # Cur != home guards against accidentally picking up the
            # user-tier path when the project is the home directory itself
            # (unusual but possible in dev environments).
            return candidate
        if cur == cur.parent:  # filesystem root
            return None
        if cur == home:
            return None
        cur = cur.parent


def load_project_hooks(workspace_root: Path | str | None) -> dict[str, list[HookConfig]]:
    if workspace_root is None:
        return {}
    path = find_project_settings_path(Path(workspace_root))
    if path is None:
        return {}
    return parse_hooks_file(path, source=HookSource.PROJECT_SETTINGS)
