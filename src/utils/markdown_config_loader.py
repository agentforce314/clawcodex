"""Generic markdown-config discovery for ``.claude/<subdir>`` directories.

Port of typescript/src/utils/markdownConfigLoader.ts. Walks managed,
user, and project directories (and ``.openclaude`` variants) to collect
``*.md`` files for a given subdir (``agents`` today; ``commands`` /
``output-styles`` later).

Loader semantics:
  * Managed dir: ``$CLAUDE_MANAGED_CONFIG_DIR/.claude/<subdir>`` (default
    ``/etc/claude``).
  * User dir: ``$CLAUDE_CONFIG_DIR/<subdir>`` (default ``~/.claude``).
  * Project dirs: walk ``cwd`` upward, stopping at the nearest ``.git``
    ancestor (or ``$HOME`` outside a git repo), collecting both
    ``.claude/<subdir>`` and ``.openclaude/<subdir>`` at every level.

Files are deduplicated by realpath so a symlinked ``~/.claude`` inside a
project tree doesn't produce duplicate entries.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.skills.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)

# Source labels used by downstream consumers to apply merge priority.
SOURCE_MANAGED = "managed"
SOURCE_USER = "user"
SOURCE_PROJECT = "project"


@dataclass(frozen=True)
class MarkdownFile:
    file_path: str
    frontmatter: dict[str, Any]
    body: str
    source: str
    base_dir: str


def _get_global_config_dir() -> Path:
    """Return ``$CLAUDE_CONFIG_DIR`` or ``~/.claude`` (resolved)."""
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def _get_managed_file_path() -> Path:
    """Return ``$CLAUDE_MANAGED_CONFIG_DIR`` or ``/etc/claude``."""
    env_override = os.environ.get("CLAUDE_MANAGED_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path("/etc/claude")


def _find_git_root(cwd: Path) -> Path | None:
    """Return the nearest ancestor containing ``.git``, or ``None``.

    Matches the boundary semantics of TS ``findGitRoot``: stops as soon as
    a ``.git`` entry (file or directory) is found. Outside any git repo,
    returns ``None`` so the walker falls back to home.
    """
    for ancestor in (cwd, *cwd.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _get_project_subdir_paths(cwd: str, subdir: str) -> list[str]:
    """Walk from ``cwd`` upward, collecting ``.claude/<subdir>`` per level.

    Generalization of src/skills/loader.py:_get_project_skills_dirs that
    matches the TS ``getProjectDirsUpToHome`` semantics: when ``cwd`` is
    inside a git repository, stop at the repo root (so parent-of-repo
    ``.claude/`` directories don't leak into the project). When not in a
    git repo, walk all the way to ``$HOME``. For each visited directory
    both ``.claude/<subdir>`` and ``.openclaude/<subdir>`` are appended so
    projects using either convention are discovered.
    """
    current = Path(cwd).expanduser().resolve()
    home = Path.home().resolve()
    git_root = _find_git_root(current)
    dirs: list[str] = []

    while True:
        for config_dir_name in (".claude", ".openclaude"):
            candidate = current / config_dir_name / subdir
            dirs.append(str(candidate))
        if current == home or current.parent == current:
            break
        if git_root is not None and current == git_root:
            break
        current = current.parent

    return list(reversed(dirs))


def _list_markdown_files(directory: str | Path) -> list[str]:
    """Recursively list ``*.md`` files under ``directory``.

    Returns ``[]`` for missing or inaccessible directories. Symlinks are
    followed (``Path.rglob`` follows them by default for the file scan,
    not for cycle detection — broken symlinks are skipped silently when
    we try to read them).
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    try:
        return sorted(str(p) for p in base.rglob("*.md") if p.is_file())
    except (OSError, PermissionError):
        return []


def _read_and_parse(file_path: str) -> tuple[dict[str, Any], str] | None:
    """Read a markdown file and return ``(frontmatter, body)`` or ``None``."""
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, PermissionError, UnicodeDecodeError) as exc:
        logger.debug("failed to read markdown file %s: %s", file_path, exc)
        return None
    result = parse_frontmatter(content)
    return result.frontmatter, result.body


def _file_identity(file_path: str) -> str | None:
    """Return ``os.path.realpath`` for dedup; ``None`` on errors (fail open)."""
    try:
        return os.path.realpath(file_path)
    except (OSError, ValueError):
        return None


def load_markdown_files_for_subdir(subdir: str, cwd: str) -> list[MarkdownFile]:
    """Discover all markdown config files for ``subdir`` across sources.

    Returns the merged list in priority order: managed → user → project.
    First-seen realpath wins (later duplicates are dropped). The caller is
    responsible for applying source-priority overrides on parsed entries.
    """
    managed_dir = str(_get_managed_file_path() / ".claude" / subdir)
    user_dir = str(_get_global_config_dir() / subdir)
    project_dirs = _get_project_subdir_paths(cwd, subdir)

    seen: set[str] = set()
    results: list[MarkdownFile] = []

    def _collect(directory: str, source: str) -> None:
        for path in _list_markdown_files(directory):
            identity = _file_identity(path) or path
            if identity in seen:
                continue
            seen.add(identity)
            parsed = _read_and_parse(path)
            if parsed is None:
                continue
            frontmatter, body = parsed
            results.append(
                MarkdownFile(
                    file_path=path,
                    frontmatter=frontmatter,
                    body=body,
                    source=source,
                    base_dir=directory,
                )
            )

    _collect(managed_dir, SOURCE_MANAGED)
    _collect(user_dir, SOURCE_USER)
    for project_dir in project_dirs:
        _collect(project_dir, SOURCE_PROJECT)

    return results


def parse_tool_list_from_cli(tools: list[str]) -> list[str]:
    """Paren-aware tool-list splitter. Port of permissionSetup.ts:814 parseToolListFromCLI.

    Splits each entry on commas AND spaces that fall OUTSIDE parentheses, so
    ``Bash(git diff:*), Read`` -> ``["Bash(git diff:*)", "Read"]`` while a comma or
    space *inside* ``(...)`` (e.g. ``Bash(git remote show:*)``) is preserved. Empty
    fragments are dropped.
    """
    result: list[str] = []
    for tool_string in tools:
        if not tool_string:
            continue
        current = ""
        in_parens = False
        for ch in tool_string:
            if ch == "(":
                in_parens = True
                current += ch
            elif ch == ")":
                in_parens = False
                current += ch
            elif ch == ",":
                if in_parens:
                    current += ch
                elif current.strip():
                    result.append(current.strip())
                    current = ""
                else:
                    current = ""
            elif ch == " ":
                if in_parens:
                    current += ch
                elif current.strip():
                    result.append(current.strip())
                    current = ""
                # else: drop a run of leading/separating spaces outside parens
            else:
                current += ch
        if current.strip():
            result.append(current.strip())
    return result


def parse_slash_command_tools_from_frontmatter(tools_value: Any) -> list[str]:
    """Port of markdownConfigLoader.ts:135 parseSlashCommandToolsFromFrontmatter.

    ``None`` -> ``[]`` (TS returns null and the caller coalesces to ``[]``); falsy
    (``""`` / empty list) -> ``[]``; a ``str`` -> single-element input; a ``list`` ->
    its string items only (non-strings dropped). A parsed ``"*"`` short-circuits to
    ``["*"]`` (wildcard: allow everything).
    """
    if tools_value is None or tools_value == "" or tools_value == []:
        return []
    if isinstance(tools_value, str):
        tools_array = [tools_value]
    elif isinstance(tools_value, list):
        tools_array = [t for t in tools_value if isinstance(t, str)]
    else:
        return []
    if not tools_array:
        return []
    parsed = parse_tool_list_from_cli(tools_array)
    if "*" in parsed:
        return ["*"]
    return parsed
