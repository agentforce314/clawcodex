"""Tests for the ``/diff`` command (Phase 11 — git-diff text, degraded).

Shows the uncommitted working-tree changes as ``git diff`` text (via ``src/utils/git.py``).
Same output-style/``/mcp`` pattern (``run()`` returns text, no ``ctx.ui``). Coexistence is
inversion (the TUI keeps its rich ``DiffDialogScreen``). Tests use a REAL ``git init`` repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.command_system import (
    DIFF_COMMAND,
    DiffCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost


def _git(tmp: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True)


def _repo(tmp: Path) -> Path:
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@example.com")
    _git(tmp, "config", "user.name", "Test")
    (tmp / "f.txt").write_text("line1\nline2\n")
    _git(tmp, "add", "f.txt")
    _git(tmp, "commit", "-m", "init")
    return tmp


def _ctx(tmp: Path, *, ui=None):
    return create_command_context(workspace_root=tmp, cwd=tmp, ui=ui)


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_diff_registered():
    assert "diff" in {c.name for c in get_builtin_commands()}
    assert "diff" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_diff_metadata_mirrors_ts():
    assert isinstance(DIFF_COMMAND, DiffCommand)
    assert DIFF_COMMAND.name == "diff"
    assert DIFF_COMMAND.description == "View uncommitted changes and per-turn diffs"
    assert DIFF_COMMAND.command_type == CommandType.INTERACTIVE


# --------------------------------------------------------------------------- #
# B. Bridge-safety
# --------------------------------------------------------------------------- #
def test_diff_blocked_from_bridge_by_type():
    assert is_bridge_safe_command(DIFF_COMMAND) is False


# --------------------------------------------------------------------------- #
# C. Modified working tree → diff text
# --------------------------------------------------------------------------- #
async def test_modified_shows_diff(tmp_path):
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("line1\nline2\nnew line\n")  # unstaged change
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert isinstance(out, InteractiveOutcome)
    assert out.message.startswith("Unstaged changes (")
    assert "+new line" in out.message
    assert out.display == "system"


async def test_staged_shows_diff(tmp_path):
    # Regression for the critic's false-negative: a STAGED change must not report
    # "No uncommitted changes." (unstaged-only `git diff` would).
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("line1\nline2\nstaged line\n")
    _git(tmp_path, "add", "f.txt")
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message.startswith("Staged changes (")
    assert "+staged line" in out.message


async def test_staged_and_unstaged_sections(tmp_path):
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("line1\nline2\nstaged\n")
    _git(tmp_path, "add", "f.txt")  # stage
    (tmp_path / "f.txt").write_text("line1\nline2\nstaged\nunstaged\n")  # further unstaged
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert "Staged changes (" in out.message
    assert "Unstaged changes (" in out.message


async def test_clean_repo(tmp_path):
    _repo(tmp_path)  # no modifications
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == "No uncommitted changes."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# E. Not a git repository (deterministic via monkeypatch)
# --------------------------------------------------------------------------- #
async def test_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.git.get_repo_root", lambda *a, **k: None)
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert out.message == "Not a git repository."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# F. Truncation of a large diff
# --------------------------------------------------------------------------- #
async def test_large_diff_truncated(tmp_path):
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("\n".join(f"line {i}" for i in range(700)) + "\n")
    out = await DIFF_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost()))
    assert "diff truncated" in out.message
    # Capped near _MAX_LINES (+ header + truncation notice line).
    assert len(out.message.split("\n")) <= 503


# --------------------------------------------------------------------------- #
# G. Engine end-to-end (headless)
# --------------------------------------------------------------------------- #
async def test_engine_succeeds_headless(tmp_path):
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("line1\nline2\nchanged\n")
    reg = CommandRegistry()
    reg.register(DIFF_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/diff")

    assert result.success is True
    assert result.text.startswith("Unstaged changes (")
