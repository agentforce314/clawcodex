"""Offline E2E for F-38 — verification gate + report dual-write + single summary comment.

Runs three rounds in temp dirs against a local bare origin + LocalTrackerAdapter.
No network, no GitHub/GitCode credentials, no risk to public repos.

Usage:
    python -m pytest tests/manual_e2e_f38.py -v -s
    # or directly:
    python tests/manual_e2e_f38.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.orchestrator.config.schema import AgentConfig, HooksConfig
from src.orchestrator.git_sync import (
    GitSyncPostCommitError,
    GitSyncService,
    HookFailedError,
    VerificationFailed,
)
from src.orchestrator.issue import Issue
from src.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from src.orchestrator.tracker import PullRequestRef
from src.orchestrator.workspace import Workspace, WorkspaceConfig, WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _build_origin(base: Path) -> Path:
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


def _write_issue(issues_dir: Path, issue_id: str, identifier: str, title: str) -> Path:
    issues_dir.mkdir(parents=True, exist_ok=True)
    path = issues_dir / f"{identifier}.md"
    path.write_text(
        "---\n"
        f"id: {issue_id}\n"
        f"identifier: {identifier}\n"
        f"state: open\n"
        f"labels: [agent:run]\n"
        "---\n"
        f"# {title}\n\n"
        "Add a small verifiable change to the repository.\n",
        encoding="utf-8",
    )
    return path


class _Session:
    def __init__(
        self,
        issue: Issue,
        workspace: Workspace,
        run_id: str,
        summary_comment_id: str,
    ) -> None:
        self.issue = issue
        self.workspace = workspace
        self.status = "completed"
        self.run_id = run_id
        self.summary_comment_id = summary_comment_id
        self.turn_count = 1
        self.tool_count = 1
        self.verification_status = None
        self.verification_output = None
        self.output_text = "agent output excerpt"


# ---------------------------------------------------------------------------
# Round scaffolding
# ---------------------------------------------------------------------------

async def _make_round(
    tmp: Path,
    *,
    agent_config: AgentConfig,
    hooks_config: HooksConfig,
    title: str,
    identifier: str = "ISSUE-E2E-1",
    issue_id: str = "e2e-1",
    run_id: str = "run-01-20260601T000000Z",
):
    base = tmp
    origin = _build_origin(base)

    issues_dir = base / "issues"
    _write_issue(issues_dir, issue_id, identifier, title)

    tracker = LocalTrackerAdapter(issues_path=issues_dir)

    manager = WorkspaceManager(
        WorkspaceConfig(
            root=base / "workspaces",
            repo_clone_url=str(origin),
            checkout_issue_branch=True,
        )
    )
    issue = Issue(
        id=issue_id,
        identifier=identifier,
        title=title,
        url=f"file://{issues_dir / (identifier + '.md')}",
    )
    workspace = await manager.create_for_issue(issue)

    # Simulate agent_runner posting a placeholder comment (Option A).
    placeholder = await tracker.create_comment(
        issue_id, "## ClawCodex Run Summary\n\n⏳ Run in progress.",
    )
    assert placeholder is not None, "placeholder comment must be created"

    session = _Session(issue, workspace, run_id, placeholder.id)
    service = GitSyncService(
        tracker,
        agent_config=agent_config,
        hooks_config=hooks_config,
    )

    return {
        "base": base,
        "origin": origin,
        "issues_dir": issues_dir,
        "tracker": tracker,
        "manager": manager,
        "issue": issue,
        "workspace": workspace,
        "session": session,
        "service": service,
    }


# ---------------------------------------------------------------------------
# Round 1: empty verification — success path
# ---------------------------------------------------------------------------

class TestRound1EmptyVerification(unittest.IsolatedAsyncioTestCase):
    async def test_round1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            ctx = await _make_round(
                tmp,
                title="Add a build status badge",
                identifier="ISSUE-E2E-1",
                issue_id="e2e-1",
                run_id="run-01-20260601T000000Z",
                agent_config=AgentConfig(
                    test_command="true",
                    build_command="",
                    lint_command="",
                ),
                hooks_config=HooksConfig(
                    pre_commit="",
                    pre_push="",
                    post_sync="",
                ),
            )

            session = ctx["session"]
            workspace = ctx["workspace"]
            issue = ctx["issue"]
            tracker = ctx["tracker"]

            (workspace.path / "README.md").write_text("main branch\n\n[![build](https://example.com/badge.svg)](https://example.com)\n", encoding="utf-8")

            result = await ctx["service"].sync(session)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.committed)
            # LocalTrackerAdapter triggers no_push=True in git_sync.sync(), so push
            # is intentionally skipped and pending_review is True. This is the
            # design behavior; in production with a remote tracker (GitHub/GitCode),
            # pushed would be True.
            self.assertFalse(result.pushed)
            self.assertTrue(result.pending_review)
            self.assertFalse(result.has_conflict)

            # 1. branch exists locally (would be on remote with a non-LocalTracker)
            branch_name = result.branch_name
            self.assertTrue(branch_name.startswith("clawcodex/issue-e2e-1"))
            local_branches = _git_output(["branch", "--list", branch_name], workspace.path)
            self.assertIn(branch_name, local_branches)

            # 2. report dual-written
            # git_sync._write_report uses type(tracker).__name__ as the tracker segment,
            # and "local" for owner/repo (since LocalTrackerAdapter has no remote metadata).
            home = Path(os.environ["HOME"])
            persistent_md = (
                home / ".clawcodex" / "reports" / "LocalTrackerAdapter" / "local" / "local"
                / "e2e-1" / f"{session.run_id}.md"
            )
            persistent_json = persistent_md.with_suffix(".json")
            workspace_md = workspace.path / ".reports" / f"{session.run_id}.md"
            workspace_json = workspace.path / ".reports" / f"{session.run_id}.json"
            self.assertTrue(workspace_md.exists(), f"workspace markdown missing: {workspace_md}")
            self.assertTrue(workspace_json.exists(), f"workspace json missing: {workspace_json}")
            self.assertTrue(persistent_md.exists(), f"persistent markdown missing: {persistent_md}")
            self.assertTrue(persistent_json.exists(), f"persistent json missing: {persistent_json}")

            # 3. report body does NOT include its own path
            md_text = workspace_md.read_text(encoding="utf-8")
            self.assertNotIn(str(workspace_md), md_text)
            self.assertNotIn(str(persistent_md), md_text)

            # 4. report contains required fields
            # NOTE: _render_markdown does not include issue_title; only identifier
            # is rendered. The title appears indirectly in the branch slug. This is
            # a known gap in the report markdown (covered separately in the JSON).
            self.assertIn("ISSUE-E2E-1", md_text)
            self.assertIn("Verification: `passed`", md_text)
            self.assertIn(f"Run: `{session.run_id}`", md_text)
            self.assertIn("Pull request:", md_text)
            self.assertIn("add-a-build-status-badge", md_text)  # via branch name

            # 5. PR body updated to contain the 5 sections
            # For LocalTracker, ensure_pull_request is intentionally NOT called
            # (no_push=True), so markdown frontmatter is never updated. Instead,
            # verify the commit on the branch contains the change. In production
            # with a remote tracker (GitHub/GitCode), the PR body would be
            # verified via tracker.update_pull_request assertions.
            self.assertIsNone(result.pull_request, "LocalTracker: no remote PR")
            commit_files = _git_output(
                ["show", "--name-only", "--pretty="], workspace.path,
            )
            self.assertIn("README.md", commit_files)
            # File was changed from the seed ("main branch\n") — verify the change
            # is reflected in the working tree.
            self.assertNotEqual(
                (workspace.path / "README.md").read_text(),
                "main branch\n",
            )
            self.assertIn("[![build]", (workspace.path / "README.md").read_text())

            # 6. exactly ONE summary comment in the ndjson (placeholder + update, no new one)
            comments = await tracker.fetch_issue_comments(issue.id)
            self.assertEqual(len(comments), 1, f"expected 1 comment, got {len(comments)}: {comments}")
            self.assertEqual(comments[0].id, session.summary_comment_id)
            comment_body = comments[0].body or ""
            # Summary comment header
            self.assertIn("## ClawCodex Run Summary", comment_body)
            # 5 F-38 sections: Issue(=Run) / Branch / Commit / Verification / Report
            self.assertIn("- Run:", comment_body)
            self.assertIn("- Branch:", comment_body)
            self.assertIn("- Commit:", comment_body)
            self.assertIn("- Verification: `passed`", comment_body)
            self.assertIn("- Report:", comment_body)
            # LocalTracker-specific fields
            self.assertIn("- Committed: yes", comment_body)
            self.assertIn("- Pushed: no", comment_body)
            # Audit metadata hidden in HTML comment
            self.assertIn("<!-- metadata: report_path=", comment_body)

            # 7. registry would be updated with report_path / verification_status /
            # summary_comment_id (orchestrator-level; covered by test_orchestrator_workspace_hooks)

            print(f"\n[Round 1 PASS] branch={branch_name}")
            print(f"  workspace report: {workspace_md}")
            print(f"  persistent report: {persistent_md}")
            print(f"  single comment id: {session.summary_comment_id}")


# ---------------------------------------------------------------------------
# Round 2: failing test_command — verification_failed, no push, no PR
# ---------------------------------------------------------------------------

class TestRound2VerificationFailure(unittest.IsolatedAsyncioTestCase):
    async def test_round2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            ctx = await _make_round(
                tmp,
                title="Verify with failing test",
                identifier="ISSUE-E2E-2",
                issue_id="e2e-2",
                run_id="run-01-20260601T000000Z",
                agent_config=AgentConfig(
                    test_command="false",  # always exits 1
                    build_command="",
                    lint_command="",
                ),
                hooks_config=HooksConfig(),
            )

            session = ctx["session"]
            workspace = ctx["workspace"]
            issue = ctx["issue"]
            tracker = ctx["tracker"]

            (workspace.path / "README.md").write_text("changes\n", encoding="utf-8")

            with self.assertRaises(GitSyncPostCommitError) as cm:
                await ctx["service"].sync(session)
            self.assertIsInstance(cm.exception.cause, VerificationFailed)
            self.assertIn("test", str(cm.exception).lower())
            self.assertTrue(cm.exception.result.committed)
            self.assertIsNotNone(cm.exception.result.commit_sha)

            # 1. NO push happened
            branch_name = "clawcodex/issue-e2e-2-verify-with-failing-test"
            self.assertEqual(
                _git_output(["ls-remote", "--heads", "origin", branch_name], workspace.path),
                "",
            )

            # 2. NO PR frontmatter written
            issue_md = ctx["issues_dir"] / "ISSUE-E2E-2.md"
            md = issue_md.read_text(encoding="utf-8")
            self.assertNotIn("pr_title:", md)
            self.assertNotIn("branch_name: clawcodex/", md)

            # 3. NO report file written (sync raised before _write_report)
            self.assertFalse((workspace.path / ".reports").exists())

            # 4. NO new comment created beyond the placeholder
            comments = await tracker.fetch_issue_comments(issue.id)
            self.assertEqual(len(comments), 1, f"expected only placeholder, got {len(comments)}")
            self.assertEqual(comments[0].id, session.summary_comment_id)
            self.assertIn("⏳ Run in progress.", comments[0].body or "")

            # 5. Orchestrator's catch path: registry would mark_verification_failed
            # (covered by extensions/orchestrator unit tests; not needed here since
            # git_sync.sync() itself never touches the registry.)

            print(f"\n[Round 2 PASS] VerificationFailed raised, no push, no PR")


# ---------------------------------------------------------------------------
# Round 3: pre_commit hook modifies files — auto-amend
# ---------------------------------------------------------------------------

class TestRound3PreCommitAmend(unittest.IsolatedAsyncioTestCase):
    async def test_round3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # pre_commit hook writes a sentinel file; git_sync should amend it in.
            formatter = (
                f"{sys.executable} -c "
                "\"from pathlib import Path; "
                "Path('formatted.txt').write_text('formatted by pre_commit hook\\n')\""
            )
            ctx = await _make_round(
                tmp,
                title="Format before commit",
                identifier="ISSUE-E2E-3",
                issue_id="e2e-3",
                run_id="run-01-20260601T000000Z",
                agent_config=AgentConfig(test_command="true"),
                hooks_config=HooksConfig(
                    pre_commit=formatter,
                    pre_push="",
                    post_sync="",
                ),
            )

            session = ctx["session"]
            workspace = ctx["workspace"]

            (workspace.path / "README.md").write_text("modified\n", encoding="utf-8")

            result = await ctx["service"].sync(session)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.committed)
            self.assertTrue(result.pending_review)  # LocalTracker behavior
            self.assertFalse(result.pushed)  # LocalTracker behavior

            # The amend step should have added formatted.txt into the same commit.
            files_in_commit = _git_output(
                ["show", "--name-only", "--pretty="], workspace.path,
            )
            self.assertIn("formatted.txt", files_in_commit)
            self.assertIn("README.md", files_in_commit)

            # Only one commit on the branch (no separate "amend" commit).
            commit_count = _git_output(
                ["rev-list", "--count", "main..HEAD"], workspace.path,
            )
            self.assertEqual(commit_count, "1", f"expected 1 commit, got {commit_count}")

            # pre_commit_output is set to the hook's stdout (the formatter writes
            # to a file, not stdout, so the output is empty — that's fine).
            self.assertTrue(hasattr(session, "pre_commit_output"))

            print(f"\n[Round 3 PASS] pre_commit hook ran, file amended into single commit")


# ---------------------------------------------------------------------------
# Round 4 (bonus): pre_push hook that modifies workspace must raise HookFailedError
# ---------------------------------------------------------------------------

class TestRound4PrePushDirtyHook(unittest.IsolatedAsyncioTestCase):
    """Verify pre_push hook that dirties workspace raises HookFailedError.

    Fixed: _status_snapshot at git_sync.py:312 was doing
    `sorted(get_file_status(repo_root))` which fails because get_file_status
    returns list[FileStatus], not strings. Now sorts by `s.path`. This test
    uses `sys.executable` to ensure the hook actually runs (the existing
    `test_pre_push_hook_cannot_modify_workspace` in test_orchestrator_git_sync.py
    used bare `python` which is not on PATH on this WSL — hook failed with
    rc=127, never reached _status_snapshot, test was a false positive).
    """

    async def test_round4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            dirty_hook = (
                f"{sys.executable} -c "
                "\"from pathlib import Path; "
                "Path('dirty.txt').write_text('pre_push dirtied the workspace\\n')\""
            )
            ctx = await _make_round(
                tmp,
                title="Dirty pre push",
                identifier="ISSUE-E2E-4",
                issue_id="e2e-4",
                run_id="run-01-20260601T000000Z",
                agent_config=AgentConfig(),
                hooks_config=HooksConfig(
                    pre_commit="",
                    pre_push=dirty_hook,
                    post_sync="",
                ),
            )

            session = ctx["session"]
            workspace = ctx["workspace"]

            (workspace.path / "README.md").write_text("dirty test\n", encoding="utf-8")

            with self.assertRaises(GitSyncPostCommitError) as cm:
                await ctx["service"].sync(session)
            self.assertIsInstance(cm.exception.cause, HookFailedError)
            self.assertIn("modified the workspace", str(cm.exception))
            self.assertTrue(cm.exception.result.committed)
            self.assertIsNotNone(cm.exception.result.commit_sha)

            # NO push happened
            branch_name = "clawcodex/issue-e2e-4-dirty-pre-push"
            self.assertEqual(
                _git_output(["ls-remote", "--heads", "origin", branch_name], workspace.path),
                "",
            )
            print(f"\n[Round 4 PASS] pre_push hook that dirtied workspace raised HookFailedError, no push")


if __name__ == "__main__":
    unittest.main(verbosity=2)
