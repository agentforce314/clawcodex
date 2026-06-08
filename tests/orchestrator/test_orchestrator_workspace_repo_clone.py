from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from extensions.orchestrator.workspace import WorkspaceManager, WorkspaceConfig


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

    async def test_default_strategy_uses_per_issue_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(root=base / "workspaces", repo_clone_url=str(origin))
            )

            workspace = await manager.create_for_issue(
                _Issue(id="125", identifier="ISSUE-125", title="Default strategy")
            )

            self.assertEqual(workspace.path, base / "workspaces" / "ISSUE-125")

    async def test_shared_and_sequential_use_root_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            for strategy in ("shared", "sequential"):
                root = base / strategy
                manager = WorkspaceManager(
                    WorkspaceConfig(
                        root=root,
                        repo_clone_url=str(origin),
                        strategy=strategy,
                        checkout_issue_branch=False,
                        sequential_lock=False,
                    )
                )

                workspace = await manager.create_for_issue(
                    _Issue(id=strategy, identifier=f"ISSUE-{strategy}", title=strategy)
                )

                self.assertEqual(workspace.path, root)
                self.assertTrue((root / ".git").exists())

    async def test_sequential_reuses_existing_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            root = base / "workspace"
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=root,
                    repo_clone_url=str(origin),
                    strategy="sequential",
                    checkout_issue_branch=False,
                )
            )

            first = await manager.create_for_issue(
                _Issue(id="1", identifier="ISSUE-1", title="First")
            )
            await manager.cleanup(first)
            second = await manager.create_for_issue(
                _Issue(id="2", identifier="ISSUE-2", title="Second")
            )

            self.assertEqual(first.path, second.path)
            self.assertTrue((root / ".git").exists())
            await manager.cleanup(second)

    async def test_sequential_creates_integration_branch_from_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            root = base / "workspace"
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=root,
                    repo_clone_url=str(origin),
                    strategy="sequential",
                    checkout_issue_branch=False,
                    base_branch="main",
                    integration_branch="integration/f42",
                    sequential_lock=False,
                )
            )

            workspace = await manager.create_for_issue(
                _Issue(id="1", identifier="ISSUE-1", title="First")
            )

            self.assertEqual(
                _git_output(["rev-parse", "--abbrev-ref", "HEAD"], workspace.path),
                "integration/f42",
            )

    async def test_sequential_fetches_integration_branch_for_existing_empty_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            seed = base / "seed"
            _git(["checkout", "-b", "integration/f42"], seed)
            (seed / "README.md").write_text("integration branch\n", encoding="utf-8")
            _git(["add", "README.md"], seed)
            _git(["commit", "-m", "integration"], seed)
            _git(["push", "-u", "origin", "integration/f42"], seed)

            root = base / "workspace"
            root.mkdir()
            _git(["init"], root)
            _git(["remote", "add", "origin", str(origin)], root)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=root,
                    repo_clone_url=str(origin),
                    strategy="sequential",
                    checkout_issue_branch=False,
                    base_branch="main",
                    integration_branch="integration/f42",
                    sequential_lock=False,
                    require_clean_start=False,
                )
            )

            workspace = await manager.create_for_issue(
                _Issue(id="1", identifier="ISSUE-1", title="First")
            )

            self.assertEqual(
                _git_output(["rev-parse", "--abbrev-ref", "HEAD"], workspace.path),
                "integration/f42",
            )
            self.assertEqual(
                (workspace.path / "README.md").read_text(encoding="utf-8"),
                "integration branch\n",
            )

    async def test_sequential_dirty_guard_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            root = base / "workspace"
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=root,
                    repo_clone_url=str(origin),
                    strategy="sequential",
                    checkout_issue_branch=False,
                )
            )

            issue = _Issue(id="1", identifier="ISSUE-1", title="First")
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            await manager.cleanup(issue)

            with self.assertRaises(Exception):
                await manager.create_for_issue(
                    _Issue(id="2", identifier="ISSUE-2", title="Second")
                )

    async def test_shared_and_sequential_cleanup_preserves_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin, _ = _build_origin_repo(base)
            for strategy in ("shared", "sequential"):
                root = base / f"{strategy}-root"
                manager = WorkspaceManager(
                    WorkspaceConfig(
                        root=root,
                        repo_clone_url=str(origin),
                        strategy=strategy,
                        checkout_issue_branch=False,
                        sequential_lock=False,
                    )
                )
                issue = _Issue(id=strategy, identifier=strategy, title=strategy)
                await manager.create_for_issue(issue)
                await manager.cleanup(issue)

                self.assertTrue(root.exists())
