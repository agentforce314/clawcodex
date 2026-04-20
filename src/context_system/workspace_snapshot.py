from __future__ import annotations

from pathlib import Path

from .models import WorkspaceSnapshot

_IGNORED_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}
_KEY_FILE_CANDIDATES = (
    "README.md",
    "CLAUDE.md",
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
    python_file_count = sum(1 for path in root.rglob("*.py") if _is_countable(path))
    test_file_count = sum(1 for path in root.rglob("test_*.py") if _is_countable(path))

    return WorkspaceSnapshot(
        workspace_root=root,
        current_directory=current,
        top_level_entries=tuple(entries),
        key_files=key_files,
        python_file_count=python_file_count,
        test_file_count=test_file_count,
    )


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_countable(path: Path) -> bool:
    return path.is_file() and not any(part in _IGNORED_NAMES for part in path.parts)
