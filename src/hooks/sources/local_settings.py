"""Local-tier settings loader: ``<workspace>/.claude/settings.local.json``.

Per the chapter: "gitignored." Used for per-developer overrides on a shared
project — e.g., a developer wants a ``PreToolUse`` audit hook locally
without checking it into team configuration.

Local settings live alongside the project settings (same ``.claude/`` dir,
different filename). The lookup also walks up from workspace root to find
the closest ``.claude/`` (matching ``project_settings``).
"""

from __future__ import annotations

from pathlib import Path

from ..hook_types import HookConfig, HookSource
from ._common import parse_hooks_file
from .project_settings import find_project_settings_path


def find_local_settings_path(start: Path) -> Path | None:
    """Walk up from ``start`` looking for ``.claude/settings.local.json``.

    Reuses ``find_project_settings_path`` to locate the ``.claude/``
    directory, then swaps the filename. This guarantees user/project/local
    all agree on which workspace ``.claude/`` they're reading from.
    """
    project_path = find_project_settings_path(start)
    if project_path is None:
        # No .claude/ dir found in the walk — try one more time looking
        # specifically for the .local.json variant (the project-tier
        # file might not exist but the local one might).
        cur = start.resolve()
        home = Path.home().resolve()
        while True:
            candidate = cur / ".claude" / "settings.local.json"
            if candidate.exists() and cur != home:
                return candidate
            if cur == cur.parent or cur == home:
                return None
            cur = cur.parent
    local = project_path.parent / "settings.local.json"
    return local if local.exists() else None


def load_local_hooks(workspace_root: Path | str | None) -> dict[str, list[HookConfig]]:
    if workspace_root is None:
        return {}
    path = find_local_settings_path(Path(workspace_root))
    if path is None:
        return {}
    return parse_hooks_file(path, source=HookSource.LOCAL_SETTINGS)
