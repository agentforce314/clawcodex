"""The ``## Runtime Context`` facts about a workspace: shape, key files, counts.

The Python/test file counts come from ONE bounded ``os.scandir`` walk that
never descends into vendored or generated directories (``node_modules``,
``.git``, virtualenvs, caches, ``.clawcodex`` worktree checkouts …) and stops
after :data:`MAX_SCANNED_DIRS` directories. The previous implementation used
``Path.rglob`` twice, which walks *everything* and filters afterwards: on a
workspace with a few front-end packages (hundreds of thousands of
``node_modules`` directories) each walk took ~20 s, and the system prompt is
built at spawn and again on every resume/clear — so opening a saved session
from the web sidebar sat on this for ~45 s. The counts are a hint for the
model, not an inventory: a bounded scan that says "N+ (partial)" is the right
trade, and it keeps the cost proportional to the project rather than to
whatever a package manager left on disk.
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path

from .models import WorkspaceSnapshot

#: Directory names the walk never enters. Vendored trees, VCS internals,
#: virtualenvs, tool caches, build output and the per-repo ``.clawcodex``
#: directory (its ``worktrees/`` holds whole extra checkouts of the repo,
#: which would count every file again per worktree).
_IGNORED_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".eggs",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".clawcodex",
    ".claude",
    "site-packages",
})

#: Upper bound on directories one snapshot visits (after pruning). Roughly a
#: few tens of milliseconds on a warm disk; past it the counts are reported as
#: partial rather than the walk running on.
MAX_SCANNED_DIRS = 2500

#: Upper bound on directory entries one snapshot reads, so a single flat
#: directory of a million files (a dataset dump next to the code) is bounded
#: the same way a deep tree is.
MAX_SCANNED_ENTRIES = 200_000

_KEY_FILE_CANDIDATES = (
    "README.md",
    "CLAWCODEX.md",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "package.json",
    "Makefile",
)


def build_workspace_snapshot(
    workspace_root: str | Path,
    *,
    cwd: str | Path | None = None,
    top_level_limit: int = 12,
    max_dirs: int = MAX_SCANNED_DIRS,
) -> WorkspaceSnapshot:
    root = Path(workspace_root).expanduser().resolve()
    current = Path(cwd).expanduser().resolve() if cwd is not None else root
    if not _is_within(current, root):
        current = root

    entries: list[str] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except Exception:
        children = []
    for child in children:
        if child.name in _IGNORED_NAMES:
            continue
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
        if len(entries) >= top_level_limit:
            break

    key_files = tuple(name for name in _KEY_FILE_CANDIDATES if (root / name).exists())
    python_file_count, test_file_count, partial = count_python_files(root, max_dirs=max_dirs)

    return WorkspaceSnapshot(
        workspace_root=root,
        current_directory=current,
        top_level_entries=tuple(entries),
        key_files=key_files,
        python_file_count=python_file_count,
        test_file_count=test_file_count,
        counts_partial=partial,
    )


def count_python_files(root: Path, *, max_dirs: int = MAX_SCANNED_DIRS) -> tuple[int, int, bool]:
    """``(python_files, test_files, partial)`` under ``root``, pruned and bounded.

    One breadth-first ``scandir`` walk: ignored directory names are skipped
    *before* they are entered (this is what makes the walk cheap — filtering
    ``rglob`` output afterwards still pays for every directory it crawled),
    symlinks are never followed (nor counted: a symlinked ``.py`` is not a
    file of this workspace), and once ``max_dirs`` directories or
    :data:`MAX_SCANNED_ENTRIES` entries have been scanned the walk stops and
    ``partial`` is True. Breadth-first so a partial
    scan still covers the project's shallow structure rather than one deep
    corner of it. Entries are visited in name order, so a partial count is
    deterministic for a given tree.
    """
    python_files = 0
    test_files = 0
    scanned = 0
    entries_seen = 0
    pending: deque[str] = deque([str(root)])
    while pending:
        if scanned >= max_dirs or entries_seen >= MAX_SCANNED_ENTRIES:
            return python_files, test_files, True
        directory = pending.popleft()
        scanned += 1
        try:
            with os.scandir(directory) as scan:
                items = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            continue
        entries_seen += len(items)
        for entry in items:
            name = entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name not in _IGNORED_NAMES:
                        pending.append(entry.path)
                    continue
                if name.endswith(".py") and entry.is_file(follow_symlinks=False):
                    python_files += 1
                    if name.startswith("test_"):
                        test_files += 1
            except OSError:
                continue
    return python_files, test_files, False


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = ["MAX_SCANNED_DIRS", "MAX_SCANNED_ENTRIES", "build_workspace_snapshot", "count_python_files"]
