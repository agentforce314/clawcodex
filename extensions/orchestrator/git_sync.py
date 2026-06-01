"""Post-run git sync for repository-backed workspaces."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.utils.git import (
    get_current_branch,
    get_default_branch,
    get_file_status,
    get_repo_root,
    _run_git,
)
from .issue import Issue
from .tracker import PullRequestRef, TrackerAdapter
from .workspace import Workspace


@dataclass(frozen=True)
class GitSyncResult:
    """Result of post-run git synchronization."""

    branch_name: str
    base_branch: str
    commit_sha: str | None = None
    pull_request: PullRequestRef | None = None
    committed: bool = False
    pushed: bool = False
    has_conflict: bool = False
    conflict_files: tuple[str, ...] = field(default_factory=tuple)
    pending_review: bool = False  # True for LocalTracker after successful commit


class GitSyncError(RuntimeError):
    """Raised when post-run git sync fails."""


class GitSyncService:
    """Perform commit, push, and PR creation after a run."""

    def __init__(
        self,
        tracker: TrackerAdapter,
        branch_prefix: str | None = None,
        gitignore_patterns: list[str] | None = None,
    ) -> None:
        self.tracker = tracker
        self._branch_prefix = branch_prefix
        self._gitignore_patterns = gitignore_patterns or [
            ".event_logs",
            "*.pyc",
            "__pycache__",
            "*.egg-info",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "*.log",
        ]

    async def sync(self, session: Any) -> GitSyncResult | None:
        workspace: Workspace = session.workspace
        issue: Issue = session.issue

        repo_root = get_repo_root(str(workspace.path))
        if not repo_root:
            return None

        # Check if tracker is LocalTrackerAdapter — skip push/PR for local-only repos
        from .local_tracker.adapter import LocalTrackerAdapter
        is_local_tracker = isinstance(self.tracker, LocalTrackerAdapter)
        no_push = is_local_tracker

        followup_pr = getattr(session, "pull_request", None)
        base_branch = getattr(session, "base_branch", None) or get_default_branch(repo_root)
        branch_name = self._ensure_work_branch(repo_root, issue, base_branch)
        changed = bool(get_file_status(repo_root))

        commit_sha: str | None = None
        committed = False
        pushed = False
        has_conflict = False
        conflict_files: tuple[str, ...] = ()
        if changed:
            self._ensure_commit_identity(repo_root)
            self._run_git_checked(["add", "-A"], repo_root)
            commit_message = self._build_commit_message(issue, followup=followup_pr is not None)
            self._run_git_checked(["commit", "-m", commit_message], repo_root)
            commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
            committed = True
            if no_push:
                # LocalTracker: no remote, skip push but record branch info
                pass
            else:
                pushed, has_conflict, conflict_files = self._push_with_recovery(
                    repo_root, branch_name,
                )
        else:
            commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
            # No staged changes but branch may have diverged from origin — still push
            if branch_name and not no_push:
                pushed, has_conflict, conflict_files = self._push_with_recovery(
                    repo_root, branch_name,
                )

        pr_ref: PullRequestRef | None = followup_pr
        if pr_ref is None and branch_name != base_branch and not no_push:
            pr_title = self._build_pr_title(issue)
            pr_body = self._build_pr_body(issue, commit_sha, branch_name, base_branch)
            pr_ref = await self.tracker.ensure_pull_request(
                issue=issue,
                head_branch=branch_name,
                base_branch=base_branch,
                title=pr_title,
                body=pr_body,
            )

        if committed or (pushed and not no_push) or pr_ref is not None:
            await self._comment_sync_result(
                issue=issue,
                branch_name=branch_name,
                base_branch=base_branch,
                commit_sha=commit_sha,
                pull_request=pr_ref,
                committed=committed,
                pushed=pushed if not no_push else False,
            )

        return GitSyncResult(
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pull_request=pr_ref,
            committed=committed,
            pushed=pushed,
            has_conflict=has_conflict,
            conflict_files=conflict_files,
            pending_review=bool(is_local_tracker and committed),
        )

    def _push_with_recovery(
        self, repo_root: str, branch_name: str,
    ) -> tuple[bool, bool, tuple[str, ...]]:
        """Push branch, recovering from non-fast-forward with rebase."""
        stdout, stderr, rc = _run_git(
            ["push", "-u", "origin", branch_name], repo_root,
        )
        if rc == 0:
            return True, False, ()

        if not self._is_non_fast_forward(stderr):
            raise GitSyncError(f"git push failed: {stderr or stdout}")

        # Attempt fetch + rebase
        self._run_git_checked(["fetch", "origin"], repo_root)
        stdout, stderr, rc = _run_git(
            ["rebase", f"origin/{branch_name}"], repo_root,
        )
        if rc != 0:
            # Check if remote branch doesn't exist (shallow clone scenario)
            if "fatal: invalid upstream" in stderr or "couldn't find remote ref" in stderr:
                # Remote branch doesn't exist - force push to create it
                self._run_git_checked(
                    ["push", "-u", "origin", branch_name, "--force"], repo_root
                )
                return True, False, ()
            conflict_files = self._detect_conflicts(repo_root)
            if conflict_files:
                return False, True, conflict_files
            raise GitSyncError(f"git rebase failed: {stderr or stdout}")

        # Retry push after successful rebase
        self._run_git_checked(["push", "-u", "origin", branch_name], repo_root)
        return True, False, ()

    def _is_non_fast_forward(self, stderr: str) -> bool:
        if not stderr:
            return False
        return (
            "non-fast-forward" in stderr.lower()
            or "fetch first" in stderr.lower()
            or "Updates were rejected" in stderr
            or "shallow update" in stderr.lower()
            or "deny updating a hidden branch" in stderr.lower()
        )

    def _detect_conflicts(self, repo_root: str) -> tuple[str, ...]:
        """Return list of files with conflict markers."""
        stdout, _, _ = _run_git(
            ["diff", "--name-only", "--diff-filter=U"], repo_root,
        )
        if not stdout.strip():
            return ()
        return tuple(f.strip() for f in stdout.strip().splitlines() if f.strip())

    def _ensure_work_branch(
        self,
        repo_root: str,
        issue: Issue,
        base_branch: str,
    ) -> str:
        current_branch = get_current_branch(repo_root)
        branch_name = issue.branch_name or self._default_branch_name(issue)

        if current_branch == branch_name:
            return branch_name
        if current_branch and current_branch != "HEAD" and current_branch != base_branch:
            return current_branch

        stdout, stderr, rc = _run_git(["checkout", branch_name], repo_root)
        if rc == 0:
            return branch_name

        # Branch doesn't exist locally — determine best creation strategy
        # Case 1: remote branch exists → checkout with --track to wire it to origin
        # Case 2: completely new branch → create with -b
        remote_ref = f"origin/{branch_name}"
        check_remote = self._run_git_output(
            ["rev-parse", "--verify", f"refs/remotes/{remote_ref}"], repo_root
        )
        if check_remote:
            # Remote branch exists — wire it up with --track
            stdout, stderr, rc = _run_git(
                ["checkout", "--track", remote_ref],
                repo_root,
            )
        else:
            # No remote branch → create new local branch
            stdout, stderr, rc = _run_git(
                ["checkout", "-b", branch_name],
                repo_root,
            )
        if rc != 0:
            raise GitSyncError(
                f"Failed to checkout work branch {branch_name}: {stderr or stdout}"
            )
        return branch_name

    def _ensure_commit_identity(self, repo_root: str) -> None:
        email = self._run_git_output(["config", "user.email"], repo_root)
        name = self._run_git_output(["config", "user.name"], repo_root)
        if not email:
            self._run_git_checked(
                ["config", "user.email", "clawcodex-bot@local.invalid"],
                repo_root,
            )
        if not name:
            self._run_git_checked(
                ["config", "user.name", "ClawCodex Bot"],
                repo_root,
            )

    def _build_commit_message(self, issue: Issue, *, followup: bool = False) -> str:
        identifier = (issue.identifier or "issue").strip()
        title = (issue.title or "automated update").strip()
        prefix = "fix" if followup else "feat"
        message = f"{prefix}: {identifier} {title}"
        return message[:72]

    def _build_pr_title(self, issue: Issue) -> str:
        identifier = (issue.identifier or "issue").strip()
        title = (issue.title or "Automated update").strip()
        return f"{identifier}: {title}"

    def _build_pr_body(
        self,
        issue: Issue,
        commit_sha: str | None,
        branch_name: str,
        base_branch: str,
    ) -> str:
        lines = [
            "## ClawCodex Automated Change",
            "",
            f"- Issue: {issue.identifier or issue.id or 'unknown'}",
            f"- Branch: `{branch_name}`",
            f"- Base: `{base_branch}`",
        ]
        if commit_sha:
            lines.append(f"- Commit: `{commit_sha}`")
        if issue.url:
            lines.append(f"- Source issue: {issue.url}")
        return "\n".join(lines)

    async def _comment_sync_result(
        self,
        *,
        issue: Issue,
        branch_name: str,
        base_branch: str,
        commit_sha: str | None,
        pull_request: PullRequestRef | None,
        committed: bool,
        pushed: bool,
    ) -> None:
        if not issue.id:
            return

        body_lines = [
            "## ClawCodex Git Sync",
            "",
            f"- Branch: `{branch_name}`",
            f"- Base: `{base_branch}`",
            f"- Committed: {'yes' if committed else 'no'}",
            f"- Pushed: {'yes' if pushed else 'no'}",
        ]
        if commit_sha:
            body_lines.append(f"- Commit: `{commit_sha}`")
        if pull_request and pull_request.url:
            body_lines.append(f"- Pull request: {pull_request.url}")
        await self.tracker.create_comment(issue.id, "\n".join(body_lines))

    def _default_branch_name(self, issue: Issue) -> str:
        identifier = issue.identifier or issue.id or "issue"
        title = issue.title or "update"
        slug = _slugify(f"{identifier}-{title}")[:48]
        prefix = self._branch_prefix or "clawcodex"
        return f"{prefix}/{slug}"

    def _run_git_output(self, args: list[str], repo_root: str) -> str:
        stdout, stderr, rc = _run_git(args, repo_root)
        if rc != 0:
            return ""
        return stdout.strip()

    def _run_git_checked(self, args: list[str], repo_root: str) -> str:
        stdout, stderr, rc = _run_git(args, repo_root)
        if rc != 0:
            raise GitSyncError(
                f"git {' '.join(args)} failed: {stderr or stdout}"
            )
        return stdout.strip()


def _slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9._/-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "issue-update"
