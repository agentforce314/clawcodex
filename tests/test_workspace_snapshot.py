"""The workspace snapshot's file walk: pruned before entering, bounded, honest.

The system prompt's ``## Runtime Context`` counts used to come from two
``Path.rglob`` passes that crawled every directory under the workspace —
``node_modules`` included — and filtered afterwards, ~20 s per system-prompt
build on a repo with a few front-end packages (built at spawn and again on
every resume, so ~45 s to open a saved session from the web sidebar).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.context_system.builder import _build_workspace_section
from src.context_system.workspace_snapshot import (
    MAX_SCANNED_DIRS,
    build_workspace_snapshot,
    count_python_files,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_counts_python_and_test_files_outside_ignored_trees(tmp_path: Path) -> None:
    _touch(tmp_path / "src" / "app.py")
    _touch(tmp_path / "src" / "pkg" / "util.py")
    _touch(tmp_path / "tests" / "test_app.py")
    _touch(tmp_path / "README.md")
    # Vendored and generated trees must neither count nor be entered.
    _touch(tmp_path / "node_modules" / "left-pad" / "setup.py")
    _touch(tmp_path / ".venv" / "lib" / "site.py")
    _touch(tmp_path / ".git" / "hooks" / "test_hook.py")
    _touch(tmp_path / ".clawcodex" / "worktrees" / "wt-1" / "src" / "app.py")

    snapshot = build_workspace_snapshot(tmp_path)

    assert snapshot.python_file_count == 3
    assert snapshot.test_file_count == 1
    assert snapshot.counts_partial is False
    assert snapshot.key_files == ("README.md",)
    assert "node_modules/" not in snapshot.top_level_entries
    assert "src/" in snapshot.top_level_entries


def test_ignored_directories_are_pruned_not_filtered(tmp_path: Path) -> None:
    """A huge ignored subtree costs nothing: the walk never steps into it.

    With a budget of three directories (root, ``src``, ``tests``) the walk
    only stays within budget if ``node_modules`` — deeper than the budget on
    its own — was skipped before being entered rather than crawled and then
    filtered out.
    """
    _touch(tmp_path / "src" / "app.py")
    _touch(tmp_path / "tests" / "test_app.py")
    deep = tmp_path / "node_modules"
    for index in range(20):
        deep = deep / f"dep-{index}"
    _touch(deep / "vendored.py")

    python_files, test_files, partial = count_python_files(tmp_path, max_dirs=3)

    assert (python_files, test_files, partial) == (2, 1, False)


def test_the_walk_stops_at_its_budget_and_says_so(tmp_path: Path) -> None:
    for index in range(10):
        _touch(tmp_path / f"pkg-{index:02d}" / "mod.py")

    python_files, _tests, partial = count_python_files(tmp_path, max_dirs=4)

    # Root plus the first three packages in name order — deterministic.
    assert partial is True
    assert python_files == 3

    snapshot = build_workspace_snapshot(tmp_path, max_dirs=4)
    assert snapshot.counts_partial is True

    section = _build_workspace_section(tmp_path, tmp_path)
    assert "- Python files: 10" in section  # the default budget covers it all
    assert "partial" not in section


def test_partial_counts_are_rendered_as_lower_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(6):
        _touch(tmp_path / f"pkg-{index}" / "test_mod.py")

    from src.context_system import workspace_snapshot

    monkeypatch.setattr(workspace_snapshot, "MAX_SCANNED_DIRS", 2)
    # The builder calls through the module-level default, so patch the
    # function's default the way a smaller budget would arrive in production.
    monkeypatch.setattr(
        workspace_snapshot,
        "build_workspace_snapshot",
        lambda root, cwd=None: build_workspace_snapshot(root, cwd=cwd, max_dirs=2),
    )

    section = _build_workspace_section(tmp_path, tmp_path)

    assert "- Python files: 1+ (partial scan)" in section
    assert "- Test files: 1+ (partial scan)" in section


def test_symlink_loops_do_not_hang_the_walk(tmp_path: Path) -> None:
    _touch(tmp_path / "src" / "app.py")
    try:
        (tmp_path / "src" / "loop").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - platform
        pytest.skip("symlinks unavailable")

    python_files, _tests, partial = count_python_files(tmp_path, max_dirs=MAX_SCANNED_DIRS)

    assert (python_files, partial) == (1, False)
