from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.orchestrator.config.schema import AgentConfig, HooksConfig
from src.orchestrator.git_sync import GitSyncService, GitSyncError, HookFailedError, VerificationFailed
from src.orchestrator.issue import Issue
from src.orchestrator.tracker import PullRequestRef, TrackerAdapter
from src.orchestrator.workspace import Workspace, WorkspaceConfig, WorkspaceManager


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


class _Comment:
    def __init__(self, id: str, body: str) -> None:
        self.id = id
        self.body = body


class _Tracker(TrackerAdapter):
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []
        self.updated_comments: list[tuple[str, str, str]] = []
        self.pr_requests: list[tuple[str, str, str, str]] = []
        self.pr_updates: list[tuple[PullRequestRef, str | None, str | None]] = []

    async def fetch_candidate_issues(self) -> list[Issue]:
        return []

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, Issue]:
        return {}

    async def create_comment(self, issue_id: str, body: str) -> _Comment:
        self.comments.append((issue_id, body))
        return _Comment(str(len(self.comments)), body)

    async def update_comment(
        self,
        issue_id: str,
        comment_id: str,
        body: str,
    ) -> _Comment | None:
        self.updated_comments.append((issue_id, comment_id, body))
        return _Comment(comment_id, body)

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        return None

    async def ensure_pull_request(
        self,
        *,
        issue: Issue,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRef | None:
        self.pr_requests.append((head_branch, base_branch, title, body))
        return PullRequestRef(
            number="9",
            url="https://example.test/pr/9",
            title=title,
        )

    async def update_pull_request(
        self,
        *,
        pull_request: PullRequestRef,
        title: str | None = None,
        body: str | None = None,
    ) -> PullRequestRef | None:
        self.pr_updates.append((pull_request, title, body))
        return PullRequestRef(
            number=pull_request.number,
            url=pull_request.url,
            title=title or pull_request.title,
        )


class _Session:
    def __init__(self, issue: Issue, workspace: Workspace) -> None:
        self.issue = issue
        self.workspace = workspace
        self.status = "completed"
        self.run_id = "run-01-20260601T000000Z"
        self.summary_comment_id = "summary-1"
        self.turn_count = 1
        self.tool_count = 1
        self.verification_status = None
        self.verification_output = None
        self.output_text = "done"


def _build_origin_repo(base: Path) -> Path:
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

    return origin


class TestGitSyncService(unittest.IsolatedAsyncioTestCase):
    async def test_sync_commits_pushes_and_creates_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    checkout_issue_branch=True,
                )
            )
            issue = Issue(
                id="77",
                identifier="ISSUE-77",
                title="Automate git sync",
                url="https://example.test/issues/77",
            )
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")

            tracker = _Tracker()
            service = GitSyncService(tracker)
            result = await service.sync(_Session(issue, workspace))

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.committed)
            self.assertTrue(result.pushed)
            self.assertEqual(result.base_branch, "main")
            self.assertTrue(result.branch_name.startswith("clawcodex/issue-77"))
            self.assertEqual(
                _git_output(["rev-parse", "--abbrev-ref", "HEAD"], workspace.path),
                result.branch_name,
            )
            self.assertEqual(
                _git_output(["ls-remote", "--heads", "origin", result.branch_name], workspace.path)
                != "",
                True,
            )
            self.assertEqual(len(tracker.pr_requests), 1)
            self.assertEqual(tracker.pr_requests[0][0], result.branch_name)
            self.assertEqual(tracker.pr_requests[0][1], "main")
            self.assertEqual(tracker.comments, [])
            self.assertEqual(len(tracker.updated_comments), 1)
            self.assertIn("Pull request: https://example.test/pr/9", tracker.updated_comments[0][2])
            self.assertEqual(len(tracker.pr_updates), 1)
            assert tracker.pr_updates[0][2] is not None
            self.assertIn("Verification: `passed`", tracker.pr_updates[0][2])
            self.assertIn("Report: `", tracker.pr_updates[0][2])

    async def test_followup_sync_reuses_existing_pr_and_uses_fix_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    checkout_issue_branch=True,
                )
            )
            issue = Issue(
                id="77",
                identifier="ISSUE-77",
                title="Automate git sync",
                branch_name="clawcodex/issue-77",
            )
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "README.md").write_text("follow-up\n", encoding="utf-8")

            session = _Session(issue, workspace)
            session.pull_request = PullRequestRef(
                number="9",
                url="https://example.test/pr/9",
            )
            session.base_branch = "main"
            tracker = _Tracker()
            service = GitSyncService(tracker)
            result = await service.sync(session)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.committed)
            self.assertTrue(result.pushed)
            self.assertEqual(result.pull_request.number, session.pull_request.number)
            self.assertEqual(result.pull_request.url, session.pull_request.url)
            self.assertEqual(tracker.pr_requests, [])
            self.assertEqual(
                _git_output(["log", "-1", "--pretty=%s"], workspace.path),
                "fix: ISSUE-77 Automate git sync",
            )
            self.assertIn("Pull request: https://example.test/pr/9", tracker.updated_comments[0][2])

    async def test_sync_push_non_fast_forward_recovers(self) -> None:
        """Non-fast-forward push triggers rebase and returns conflict state."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)

            work_a = base / "work_a"
            work_b = base / "work_b"
            _git(["clone", str(origin), str(work_a)], base)
            _git(["clone", str(origin), str(work_b)], base)
            _git(["config", "user.email", "a@example.com"], work_a)
            _git(["config", "user.name", "A"], work_a)
            _git(["config", "user.email", "b@example.com"], work_b)
            _git(["config", "user.name", "B"], work_b)

            # A force-pushes stale origin/main
            (work_a / "file.txt").write_text("from A\n")
            _git(["add", "file.txt"], work_a)
            _git(["commit", "-m", "from A"], work_a)
            _git(["push", "-f", "origin", "main"], work_a)

            # B makes a conflicting commit on stale origin/main
            (work_b / "file.txt").write_text("from B\n")
            _git(["add", "file.txt"], work_b)
            _git(["commit", "-m", "from B"], work_b)

            issue = Issue(
                id="99",
                identifier="ISSUE-99",
                title="Conflict recovery test",
                url="https://example.test/issues/99",
                branch_name="main",
            )

            class _FakeWorkspace:
                def __init__(self, path: Path) -> None:
                    self.path = path

            class _FakeSession:
                def __init__(self, ws: _FakeWorkspace, iss: Issue) -> None:
                    self.workspace = ws
                    self.issue = iss

            session = _FakeSession(_FakeWorkspace(work_b), issue)
            tracker = _Tracker()
            service = GitSyncService(tracker)
            result = await service.sync(session)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result.pushed)
            self.assertTrue(result.has_conflict)
            self.assertGreater(len(result.conflict_files), 0)

    async def test_pre_push_verification_failure_prevents_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    checkout_issue_branch=True,
                )
            )
            issue = Issue(id="77", identifier="ISSUE-77", title="Verify before push")
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")

            service = GitSyncService(
                _Tracker(),
                agent_config=AgentConfig(test_command="python -c 'raise SystemExit(7)'"),
            )

            with self.assertRaises(VerificationFailed):
                await service.sync(_Session(issue, workspace))
            self.assertEqual(
                _git_output(["ls-remote", "--heads", "origin", "clawcodex/issue-77-verify-before-push"], workspace.path),
                "",
            )

    async def test_pre_commit_hook_modifies_files_and_amends_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    checkout_issue_branch=True,
                )
            )
            issue = Issue(id="77", identifier="ISSUE-77", title="Format before commit")
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")

            service = GitSyncService(
                _Tracker(),
                hooks_config=HooksConfig(pre_commit=f"{sys.executable} -c \"from pathlib import Path; Path('formatted.txt').write_text('ok\\n')\""),
            )
            await service.sync(_Session(issue, workspace))

            self.assertIn("formatted.txt", _git_output(["show", "--name-only", "--pretty="], workspace.path))

    async def test_pre_push_hook_cannot_modify_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = _build_origin_repo(base)
            manager = WorkspaceManager(
                WorkspaceConfig(
                    root=base / "workspaces",
                    repo_clone_url=str(origin),
                    checkout_issue_branch=True,
                )
            )
            issue = Issue(id="77", identifier="ISSUE-77", title="Dirty pre push")
            workspace = await manager.create_for_issue(issue)
            (workspace.path / "README.md").write_text("changed\n", encoding="utf-8")

            service = GitSyncService(
                _Tracker(),
                hooks_config=HooksConfig(pre_push="python -c \"from pathlib import Path; Path('dirty.txt').write_text('dirty\\n')\""),
            )

            with self.assertRaises(HookFailedError):
                await service.sync(_Session(issue, workspace))
