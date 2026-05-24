from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from src.orchestrator.workspace import WorkspaceManager, WorkspaceConfig


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass
class _Issue:
    id: str
    identifier: str
    title: str
    branch_name: str | None = None


def _build_origin_repo(base: Path) -> tuple[Path, str]:
    origin = base / "origin.git"
    seed = base / "seed"
    seed.mkdir(parents=True)

    _git(["init", "--bare", str(origin)], base)
    _git(["init"], seed)
    _git(["config", "user.email", "test@example.com"], seed)
    _git(["config", "user.name", "Test User"], seed)

    (seed / "README.md").write_text("main branch\n", encoding="utf-8")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "initial"], seed)
    _git(["branch", "-M", "main"], seed)
    _git(["remote", "add", "origin", str(origin)], seed)
    _git(["push", "-u", "origin", "main"], seed)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], origin)

    _git(["checkout", "-b", "feature/issue-123"], seed)
    (seed / "README.md").write_text("feature branch\n", encoding="utf-8")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "feature"], seed)
    _git(["push", "-u", "origin", "feature/issue-123"], seed)
    _git(["checkout", "main"], seed)

    return origin, "feature/issue-123"


class TestWorkspaceRepositoryClone(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_clones_repo_and_checks_out_issue_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, branch_name = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    clone_depth=1,
                    checkout_issue_branch=True,
                )
            )

            issue = _Issue(
                id="123",
                identifier="ISSUE-123",
                title="Test issue",
                branch_name=branch_name,
            )
            workspace = await manager.create_for_issue(issue)

            self.assertTrue((workspace.path / ".git").exists())
            self.assertEqual(
                _git_output(["rev-parse", "--abbrev-ref", "HEAD"], workspace.path),
                branch_name,
            )
            self.assertEqual(
                (workspace.path / "README.md").read_text(encoding="utf-8"),
                "feature branch\n",
            )

    async def test_workspace_clones_repo_without_branch_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    clone_depth=1,
                    checkout_issue_branch=True,
                )
            )

            issue = _Issue(
                id="124",
                identifier="ISSUE-124",
                title="Default branch issue",
            )
            workspace = await manager.create_for_issue(issue)

            self.assertTrue((workspace.path / ".git").exists())
            self.assertEqual(
                (workspace.path / "README.md").read_text(encoding="utf-8"),
                "main branch\n",
            )
