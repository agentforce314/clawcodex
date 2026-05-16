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


def _find_canonical_git_root(cwd: Path) -> Path | None:
    """Return the *main* repo root, resolving worktree ``.git`` files.

    Direct port of TS ``resolveCanonicalRoot`` in ``utils/git.ts:122-180``,
    including the security validation. Returns ``None`` outside any git
    repo. For a regular checkout returns the same path as ``_find_git_root``.
    For a worktree, follows the chain:

        gitRoot/.git  → "gitdir: <worktreeGitDir>"
        worktreeGitDir/commondir → "<relative path to main .git>"

    and validates both back-links before trusting them. **The ``.git`` file
    and ``commondir`` are attacker-controlled** in any cloned repo; without
    these checks a hostile repo could point ``commondir`` at any path the
    victim has trusted and bypass the trust dialog. We require:

      1. ``realpath(dirname(worktreeGitDir))`` equals
         ``commonDir / "worktrees"`` — ensures the ``commondir`` file we
         read lives inside the resolved common dir, not inside the
         attacker's repo.
      2. ``realpath(worktreeGitDir/gitdir)`` equals
         ``realpath(gitRoot) / ".git"`` — ensures an attacker can't borrow
         a victim's existing worktree entry by guessing its path.

    Any check failure or unexpected error → return the input ``gitRoot``
    (treat as a standalone repo, no fallback).

    **Known limitation (matches TS):** these checks raise the bar but do
    not constitute a hard sandbox. An attacker who controls both the
    worktree gitdir layout AND can write a back-link pointing at the
    victim's ``gitRoot/.git`` (e.g., a guessed path) can still pass both
    checks. The TS reference has the same residual hole — a fuller fix
    would require a trust-list of canonical repo roots maintained by the
    caller. Track future hardening separately.
    """
    git_root = _find_git_root(cwd)
    if git_root is None:
        return None
    git_path = git_root / ".git"
    if git_path.is_dir():
        return git_root  # regular checkout
    try:
        git_contents = git_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return git_root
    if not git_contents.startswith("gitdir:"):
        return git_root
    target = git_contents[len("gitdir:"):].strip()
    if not target:
        return git_root
    worktree_gitdir_raw = Path(target)
    if not worktree_gitdir_raw.is_absolute():
        worktree_gitdir_raw = (git_root / worktree_gitdir_raw)
    try:
        worktree_gitdir = worktree_gitdir_raw.resolve(strict=False)
    except (OSError, ValueError):
        return git_root

    # Read commondir back-link (relative path to the shared ``.git`` dir).
    # Submodules have no ``commondir`` and fall through to git_root.
    commondir_path = worktree_gitdir / "commondir"
    try:
        commondir_rel = commondir_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return git_root
    if not commondir_rel:
        return git_root
    try:
        common_dir = (worktree_gitdir / commondir_rel).resolve(strict=False)
    except (OSError, ValueError):
        return git_root

    # SECURITY check 1: worktree gitdir must be a direct child of
    # ``<commonDir>/worktrees``. Realpath dirname so symlinked /tmp →
    # /private/tmp doesn't break the check.
    try:
        parent_real = worktree_gitdir.parent.resolve(strict=False)
    except (OSError, ValueError):
        return git_root
    expected_parent = common_dir / "worktrees"
    if parent_real != expected_parent:
        return git_root

    # SECURITY check 2: ``<worktreeGitDir>/gitdir`` back-link must
    # realpath-equal ``realpath(git_root) / ".git"``. Realpath the dir
    # then join '.git' — realpathing the .git file itself would follow
    # an attacker's symlinked .git and let them borrow a victim's
    # back-link.
    backlink_path = worktree_gitdir / "gitdir"
    try:
        backlink_target = Path(backlink_path.read_text(encoding="utf-8").strip())
        backlink_real = backlink_target.resolve(strict=False)
        git_root_real = git_root.resolve(strict=False) / ".git"
    except (OSError, UnicodeDecodeError, ValueError):
        return git_root
    if backlink_real != git_root_real:
        return git_root

    # Bare-repo worktrees: common_dir isn't inside a working directory;
    # use common_dir itself as the stable identity.
    if common_dir.name != ".git":
        return common_dir
    return common_dir.parent


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

    # Worktree fallback: when ``cwd`` is in a git worktree and the worktree's
    # own ``.claude/<subdir>``/``.openclaude/<subdir>`` aren't checked out
    # (e.g. sparse-checkout), append the *main* repo's copies so they remain
    # reachable. Mirrors markdownConfigLoader.ts:324-341 — only triggers when
    # the canonical (main) root differs from the worktree root.
    cwd_path = Path(cwd).expanduser().resolve()
    git_root = _find_git_root(cwd_path)
    canonical_root = _find_canonical_git_root(cwd_path)
    if (
        git_root is not None
        and canonical_root is not None
        and canonical_root != git_root
    ):
        worktree_dirs = {
            str(git_root / config_dir / subdir)
            for config_dir in (".claude", ".openclaude")
        }
        worktree_has_subdir = any(
            Path(d).is_dir() for d in project_dirs if d in worktree_dirs
        )
        if not worktree_has_subdir:
            for config_dir_name in (".claude", ".openclaude"):
                fallback = str(canonical_root / config_dir_name / subdir)
                if fallback not in project_dirs:
                    project_dirs.append(fallback)

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
