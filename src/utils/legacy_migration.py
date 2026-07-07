"""One-time migration of legacy ``.claude`` state into ``.clawcodex``.

Before the directory rebrand, several subsystems read/wrote the real
Claude Code harness's locations (``~/.claude/skills``, ``~/.claude/
projects/<slug>/memory``, ``<project>/.claude/workflows``, ...). The
rebrand hard-cuts every runtime path to ``.clawcodex``; this module
preserves what those subsystems could previously see by COPYING it to
the new home once.

Invariants (the whole safety story):
  * The legacy tree is a SOURCE only. Nothing under ``~/.claude`` or
    ``<project>/.claude`` is ever moved, modified, or deleted — the real
    Claude Code installation on the same machine keeps working.
  * Copies happen only when the destination is absent. An existing
    ``~/.clawcodex/<item>`` always wins; re-running is a no-op.
  * User-level migration runs once per config home, gated by a marker
    file (``.claude-migration.json``) that records what happened. The
    marker is written even on partial failure so a broken source can't
    retry-loop every startup; ``clawcodex migrate`` re-attempts
    explicitly.
  * ``migrate_user_dir_once()`` must never break startup: every failure
    is caught and recorded.
  * Concurrent first-starts (e.g. CLI + agent-server racing past the
    marker check) are benign: copies are additive and destination-owned
    (the loser of a per-item race records a spurious error/skip in its
    report while the winner's copy stands), and the marker write is an
    atomic replace, so the state converges regardless of ordering.

Project-level migration (``migrate_project_dir``) mutates the user's
repository (creates ``.clawcodex/`` copies), so it is NEVER automatic —
only the explicit ``clawcodex migrate`` subcommand runs it.

Deliberately NOT migrated:
  * ``settings.json`` / ``settings.local.json`` (user or project) — on a
    machine with both tools these hold the OTHER harness's live
    permission grants and hooks; silently importing foreign grants is a
    security decision the user must make by copying the file themselves.
    (clawcodex user settings already live at ``~/.clawcodex/settings.json``.)
  * ``.claude/worktrees/`` — git worktrees are registered in ``.git``
    with absolute paths; a file copy produces broken trees. Existing
    worktrees keep working where they are.
  * Session state (``projects/<slug>/*.jsonl`` transcripts, todos,
    shell-snapshots, ...) — real-Claude-Code session data, not clawcodex's.
  * Any skill directory larger than ``_MAX_SKILL_DIR_BYTES`` (a
    ``node_modules``-scale toolchain like gstack is an installation, not
    a config file; recorded as skipped with a manual ``cp -R`` hint).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.utils.clawcodex_dirs import (
    PROJECT_DIR_NAME,
    LEGACY_PROJECT_DIR_NAME,
    get_legacy_user_config_dir,
    get_user_config_dir,
)

logger = logging.getLogger(__name__)

MARKER_FILENAME = ".claude-migration.json"

#: Per-skill-directory copy cap. ``~/.claude/skills/<name>`` entries are
#: normally a SKILL.md plus a few scripts; anything beyond this is a
#: vendored toolchain that should be reinstalled, not duplicated.
_MAX_SKILL_DIR_BYTES = 50 * 1024 * 1024

#: User-level items copied whole (dir or file) when the destination is
#: absent. ``skills`` is handled separately (per-child, size-capped).
_USER_LEVEL_DIRS = ("agents", "workflows", "outputStyles", "plugins", "rules")
_USER_LEVEL_FILES = ("CLAUDE.md",)

#: Project-level items for the explicit ``clawcodex migrate`` command.
_PROJECT_LEVEL_DIRS = ("skills", "agents", "workflows", "rules")
_PROJECT_LEVEL_FILES = ("CLAUDE.md", "config.json", "config.local.json", "loop.md")
_PROJECT_SKIPPED_SETTINGS = ("settings.json", "settings.local.json")


@dataclass
class MigrationReport:
    """What a migration pass did. Serialized into the marker file."""

    source: str
    destination: str
    copied: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "source": self.source,
            "destination": self.destination,
            "copied": self.copied,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _dir_size_bytes(path: Path, cap: int) -> int:
    """Apparent size of *path*, short-circuiting once *cap* is exceeded.

    Never follows symlinks (a link into a huge tree counts as the link
    itself, matching what ``copytree(symlinks=True)`` would copy).
    """
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += st.st_size
                    if total > cap:
                        return total
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
        except OSError:
            continue
    return total


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore_dangling_symlinks=True,
    )


def _copy_item(src: Path, dst: Path, label: str, report: MigrationReport) -> None:
    """Copy one file/dir when the destination is absent. Never raises."""
    try:
        if not src.exists() and not src.is_symlink():
            return
        # lexists: an existing destination — even a broken symlink — wins.
        if os.path.lexists(dst):
            report.skipped.append(
                {"item": label, "reason": "destination already exists"}
            )
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir() and not src.is_symlink():
            _copy_tree(src, dst)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        report.copied.append(label)
    except Exception as exc:  # noqa: BLE001 — migration must never propagate
        report.errors.append({"item": label, "error": str(exc)})


def _migrate_skills(src_skills: Path, dst_skills: Path, report: MigrationReport) -> None:
    """Per-child skills copy with the size cap.

    Children are individual skill dirs (or loose files); each is copied
    independently so one oversized toolchain doesn't block the rest.
    """
    try:
        children = sorted(src_skills.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        report.errors.append({"item": "skills", "error": str(exc)})
        return
    for child in children:
        label = f"skills/{child.name}"
        try:
            if child.is_dir() and not child.is_symlink():
                size = _dir_size_bytes(child, _MAX_SKILL_DIR_BYTES)
                if size > _MAX_SKILL_DIR_BYTES:
                    report.skipped.append(
                        {
                            "item": label,
                            "reason": (
                                "exceeds the "
                                f"{_MAX_SKILL_DIR_BYTES // (1024 * 1024)}MB "
                                "per-skill cap; copy manually with "
                                f"`cp -R {child} {dst_skills / child.name}` "
                                "if you want it in clawcodex"
                            ),
                        }
                    )
                    continue
        except OSError as exc:
            report.errors.append({"item": label, "error": str(exc)})
            continue
        _copy_item(child, dst_skills / child.name, label, report)


def _migrate_project_memories(src_home: Path, dst_home: Path, report: MigrationReport) -> None:
    """Copy ``projects/<slug>/memory`` subtrees (auto-memory) only.

    The sibling files under each slug are real-Claude-Code session
    transcripts — explicitly not ours to copy.
    """
    projects = src_home / "projects"
    try:
        slugs = sorted(p for p in projects.iterdir() if p.is_dir())
    except OSError:
        return
    for slug_dir in slugs:
        src_mem = slug_dir / "memory"
        try:
            if not src_mem.is_dir() or not any(src_mem.iterdir()):
                continue
        except OSError:
            continue
        label = f"projects/{slug_dir.name}/memory"
        _copy_item(src_mem, dst_home / "projects" / slug_dir.name / "memory", label, report)


def _write_marker(marker: Path, report: MigrationReport) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_name(marker.name + ".tmp")
        tmp.write_text(
            json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, marker)
    except OSError:
        logger.debug("could not write migration marker %s", marker, exc_info=True)


def migrate_user_dir_once(*, force: bool = False) -> MigrationReport | None:
    """Copy legacy ``~/.claude`` user state into the clawcodex home, once.

    Returns the report when a migration pass ran, ``None`` when the
    marker short-circuited it. Startup callers wrap this in a broad
    try/except and ignore the result beyond logging.

    ``CLAWCODEX_DISABLE_LEGACY_MIGRATION`` (truthy) disables the pass
    entirely — the test suite sets it (tests/conftest.py) so tests that
    drive real entrypoints can't migrate the developer's actual home,
    and ops can set it to pin a frozen config dir.
    """
    if os.environ.get("CLAWCODEX_DISABLE_LEGACY_MIGRATION", "").lower() in (
        "1", "true", "yes",
    ):
        return None
    dst_home = get_user_config_dir()
    marker = dst_home / MARKER_FILENAME
    if marker.exists() and not force:
        return None

    src_home = get_legacy_user_config_dir()
    report = MigrationReport(source=str(src_home), destination=str(dst_home))

    try:
        same = src_home.exists() and dst_home.exists() and src_home.samefile(dst_home)
    except OSError:
        same = False
    if not src_home.is_dir() or same:
        report.skipped.append(
            {
                "item": "*",
                "reason": "no legacy ~/.claude directory"
                if not src_home.is_dir()
                else "legacy and clawcodex homes are the same directory",
            }
        )
        # Self-aliased case (CLAWCODEX_CONFIG_DIR=~/.claude): writing the
        # marker would violate "nothing under ~/.claude is ever modified",
        # so skip it — the samefile re-check each startup is one stat.
        if not same:
            _write_marker(marker, report)
        return report

    if (src_home / "skills").is_dir():
        _migrate_skills(src_home / "skills", dst_home / "skills", report)
    for name in _USER_LEVEL_DIRS:
        _copy_item(src_home / name, dst_home / name, name, report)
    for name in _USER_LEVEL_FILES:
        _copy_item(src_home / name, dst_home / name, name, report)
    _migrate_project_memories(src_home, dst_home, report)

    _write_marker(marker, report)
    if report.copied:
        logger.info(
            "[migration] copied %d legacy ~/.claude item(s) into %s "
            "(details: %s)",
            len(report.copied),
            dst_home,
            marker,
        )
    return report


def migrate_project_dir(cwd: str | os.PathLike[str]) -> MigrationReport:
    """Copy a project's ``.claude/`` config into ``.clawcodex/``.

    Explicit-only (mutates the user's repo). Idempotent via the
    destination-absent rule; no marker file.
    """
    base = Path(cwd)
    src_dir = base / LEGACY_PROJECT_DIR_NAME
    dst_dir = base / PROJECT_DIR_NAME
    report = MigrationReport(source=str(src_dir), destination=str(dst_dir))

    if not src_dir.is_dir():
        report.skipped.append({"item": "*", "reason": "no .claude directory here"})
        return report

    for name in _PROJECT_LEVEL_DIRS:
        _copy_item(src_dir / name, dst_dir / name, name, report)
    for name in _PROJECT_LEVEL_FILES:
        _copy_item(src_dir / name, dst_dir / name, name, report)
    for name in _PROJECT_SKIPPED_SETTINGS:
        if (src_dir / name).exists():
            report.skipped.append(
                {
                    "item": name,
                    "reason": (
                        "settings hold the real Claude Code harness's live "
                        "permission grants/hooks — copy manually only if this "
                        f"repo's {LEGACY_PROJECT_DIR_NAME}/{name} was written "
                        "for clawcodex"
                    ),
                }
            )
    if (src_dir / "worktrees").is_dir():
        report.skipped.append(
            {
                "item": "worktrees",
                "reason": (
                    "git worktrees are registered in .git with absolute paths; "
                    "existing ones keep working in place — new ones are created "
                    f"under {PROJECT_DIR_NAME}/worktrees"
                ),
            }
        )
    return report


def format_report(report: MigrationReport) -> str:
    """Human-readable summary for the ``clawcodex migrate`` subcommand."""
    lines = [f"{report.source} -> {report.destination}"]
    for item in report.copied:
        lines.append(f"  copied  {item}")
    for entry in report.skipped:
        lines.append(f"  skipped {entry['item']}: {entry['reason']}")
    for entry in report.errors:
        lines.append(f"  ERROR   {entry['item']}: {entry['error']}")
    if not report.copied and not report.skipped and not report.errors:
        lines.append("  nothing to do")
    return "\n".join(lines)


__all__ = [
    "MARKER_FILENAME",
    "MigrationReport",
    "migrate_user_dir_once",
    "migrate_project_dir",
    "format_report",
]
