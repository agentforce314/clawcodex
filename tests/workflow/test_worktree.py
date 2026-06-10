"""Tests for per-agent worktree isolation (#5)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.workflow.worktree import agent_worktree, worktree_slug


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_worktree_slug():
    assert worktree_slug("wf_abc123def", "0") == "wf_abc123def-0"
    # nested call-path keys (dots) become a clean filesystem slug
    assert worktree_slug("wf_abc123def", "0.1.2") == "wf_abc123def-0-1-2"


def test_agent_worktree_creates_and_removes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "tester"], repo)
    (repo / "f.txt").write_text("hi", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "init"], repo)

    captured = None
    with agent_worktree("wf_test1234567", "0", str(repo)) as wt:
        captured = wt
        assert wt is not None
        assert Path(wt).is_dir()
        assert Path(wt).name == "wf_test1234567-0"  # the wf_<runId>-<idx> slug
        assert (Path(wt) / "f.txt").exists()  # checked out at HEAD

    assert captured is not None
    assert not Path(captured).exists()  # removed on context exit


def test_agent_worktree_non_git_yields_none(tmp_path):
    d = tmp_path / "notgit"
    d.mkdir()
    with agent_worktree("wf_x", "0", str(d)) as wt:
        assert wt is None  # not a git repo -> best-effort, run in place
