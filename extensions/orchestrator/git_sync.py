"""Post-run git sync for repository-backed workspaces."""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.utils.git import (
    get_current_branch,
    get_default_branch,
    get_file_status,
    get_repo_root,
    _run_git,
)
from .config.schema import AgentConfig, HooksConfig
from . import report_writer
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


class VerificationFailed(GitSyncError):
    """Raised when configured verification commands fail."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class HookFailedError(GitSyncError):
    """Raised when a configured sync hook fails."""

    def __init__(self, hook_name: str, message: str, output: str = "") -> None:
        super().__init__(message)
        self.hook_name = hook_name
        self.output = output


class GitSyncPostCommitError(GitSyncError):
    """Raised when post-commit sync steps fail after a commit exists."""

    def __init__(self, cause: VerificationFailed | HookFailedError, result: GitSyncResult) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.result = result
        self.output = getattr(cause, "output", "")
        self.hook_name = getattr(cause, "hook_name", None)


class GitSyncService:
    """Perform commit, push, and PR creation after a run."""

    def __init__(
        self,
        tracker: TrackerAdapter,
        branch_prefix: str | None = None,
        gitignore_patterns: list[str] | None = None,
        agent_config: AgentConfig | None = None,
        hooks_config: HooksConfig | None = None,
    ) -> None:
        self.tracker = tracker
        self._branch_prefix = branch_prefix
        self._agent_config = agent_config or AgentConfig()
        self._hooks_config = hooks_config or HooksConfig()
        self._gitignore_patterns = gitignore_patterns or [
            ".event_logs",
            ".event_streams",
            ".orchestrator_control",
            ".operator_hints.md",
            ".reports",
            ".clawcodex_clarification_queue.json",
            ".clawcodex_issue_registry.json",
            ".clawcodex_workspace.lock",
            "*.pyc",
            "__pycache__",
            "*.egg-info",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "*.log",
        ]

    async def sync(
        self,
        session: Any,
        *,
        mode: str = "default",
    ) -> GitSyncResult | None:
        """Commit/push/PR sync.

        F-39 Sub-C: when `mode == "followup"`, the session is expected
        to already carry a `pull_request` attribute (set by the
        orchestrator from the registry record) and the run is treated
        as a same-branch follow-up commit. The commit message uses
        the "fix:" prefix (vs. "feat:" for new runs) and the existing
        `update_pull_request` path appends a `## ClawCodex Follow-up
        #N` section to the PR body (F-38 Sub-C, already in place).

        Other modes (default / future) are unchanged.
        """
        # F-39 Sub-C: validate followup-mode prerequisites BEFORE any
        # workspace / repo_root I/O. A follow-up that forgot to wire
        # the existing PR would otherwise silently open a brand-new
        # PR, which is exactly what follow-up is trying to avoid.
        if mode == "followup":
            existing_pr = getattr(session, "pull_request", None)
            if existing_pr is None:
                raise GitSyncError(
                    "GitSyncService.sync(mode='followup') requires "
                    "session.pull_request to be set; orchestrator "
                    "should populate it from the IssueRegistry record"
                )

        workspace: Workspace = session.workspace
        issue: Issue = session.issue

        repo_root = get_repo_root(str(workspace.path))
        if not repo_root:
            return None

        # Check if tracker is LocalTrackerAdapter — skip push/PR for local-only repos
        from .local_tracker.adapter import LocalTrackerAdapter
        is_local_tracker = isinstance(self.tracker, LocalTrackerAdapter)
        workspace_strategy = getattr(session, "workspace_strategy", "isolated")
        is_sequential = workspace_strategy == "sequential"
        if is_sequential:
            self._sync_git_exclude(repo_root)
        else:
            self._sync_gitignore(repo_root)
        no_push = is_local_tracker or is_sequential

        followup_pr = getattr(session, "pull_request", None)
        base_branch = getattr(session, "base_branch", None) or get_default_branch(repo_root)
        if is_sequential:
            branch_name = (
                getattr(session, "integration_branch", None)
                or get_current_branch(repo_root)
                or base_branch
            )
        else:
            branch_name = self._ensure_work_branch(repo_root, issue, base_branch)
        changed = bool(get_file_status(repo_root))

        commit_sha: str | None = None
        committed = False
        has_run_commit = False
        pushed = False
        has_conflict = False
        conflict_files: tuple[str, ...] = ()
        if changed:
            self._ensure_commit_identity(repo_root)
            if is_sequential:
                await self._run_pre_commit_hook(repo_root, session)
            self._run_git_checked(["add", "-A"], repo_root)
            commit_message = self._build_commit_message(issue, followup=followup_pr is not None)
            self._run_git_checked(["commit", "-m", commit_message], repo_root)
            commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
            committed = True
            try:
                if not is_sequential:
                    await self._run_pre_commit_hook(repo_root, session)
                    commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
                await self._run_pre_push_verification(repo_root, session)
            except (VerificationFailed, HookFailedError) as exc:
                # Roll back the just-created commit since verification failed
                try:
                    self._run_git_checked(["reset", "--mixed", "HEAD~1"], repo_root)
                except GitSyncError:
                    pass  # No commit to rollback or reset failed — proceed anyway
                committed = False
                raise self._post_commit_error(
                    exc,
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commit_sha=commit_sha,
                    committed=committed,
                    pushed=pushed,
                    has_conflict=has_conflict,
                    conflict_files=conflict_files,
                    pull_request=followup_pr,
                    is_local_tracker=is_local_tracker,
                ) from exc
            if no_push:
                # LocalTracker: no remote, skip push but record branch info
                pass
            else:
                pushed, has_conflict, conflict_files = self._push_with_recovery(
                    repo_root, branch_name,
                )
        else:
            commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
            start_commit_sha = getattr(session, "start_commit_sha", None)
            has_run_commit = bool(start_commit_sha and commit_sha != start_commit_sha)
            try:
                await self._run_pre_push_verification(repo_root, session)
            except (VerificationFailed, HookFailedError) as exc:
                # No new commit was created in this run, but HEAD may
                # already carry the issue's implementation from a prior
                # run on the same branch. Surface the existing HEAD as
                # the registerable commit so the orchestrator's
                # GitSyncPostCommitError handler can call mark_synced()
                # with it, instead of dropping the commit_sha entirely.
                raise self._post_commit_error(
                    exc,
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commit_sha=commit_sha,
                    committed=has_run_commit,
                    pushed=False,
                    has_conflict=False,
                    conflict_files=(),
                    pull_request=followup_pr,
                    is_local_tracker=is_local_tracker,
                ) from exc
            # No staged changes but branch may have diverged from origin — still push
            if branch_name and not no_push:
                pushed, has_conflict, conflict_files = self._push_with_recovery(
                    repo_root, branch_name,
                )

        pr_ref: PullRequestRef | None = followup_pr
        pr_title = self._build_pr_title(issue)
        if pr_ref is None and branch_name != base_branch and not no_push:
            pr_ref = await self.tracker.ensure_pull_request(
                issue=issue,
                head_branch=branch_name,
                base_branch=base_branch,
                title=pr_title,
                body=self._build_pr_body(
                    issue,
                    commit_sha,
                    branch_name,
                    base_branch,
                    session=session,
                    pull_request=None,
                ),
            )

        report_result = self._write_report(
            session=session,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pull_request=pr_ref,
        )

        if pr_ref is not None and not no_push:
            updated_pr = await self.tracker.update_pull_request(
                pull_request=pr_ref,
                title=pr_title,
                body=self._build_pr_body(
                    issue,
                    commit_sha,
                    branch_name,
                    base_branch,
                    session=session,
                    pull_request=pr_ref,
                ),
            )
            if updated_pr is not None:
                pr_ref = updated_pr
                self._write_report(
                    session=session,
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commit_sha=commit_sha,
                    pull_request=pr_ref,
                )

        has_reviewable_commit = committed or has_run_commit
        try:
            await self._run_post_sync_hook(repo_root, session)
        except (VerificationFailed, HookFailedError) as exc:
            if has_reviewable_commit:
                raise self._post_commit_error(
                    exc,
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commit_sha=commit_sha,
                    committed=has_reviewable_commit,
                    pushed=pushed,
                    has_conflict=has_conflict,
                    conflict_files=conflict_files,
                    pull_request=pr_ref,
                    is_local_tracker=is_local_tracker,
                ) from exc
            raise

        if has_reviewable_commit or (pushed and not no_push) or pr_ref is not None:
            await self._update_summary_comment(
                session=session,
                branch_name=branch_name,
                base_branch=base_branch,
                commit_sha=commit_sha,
                pull_request=pr_ref,
                committed=has_reviewable_commit,
                pushed=pushed if not no_push else False,
                report_path=(
                    report_result.persistent_markdown_path
                    if report_result is not None
                    else None
                ),
            )

        return GitSyncResult(
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pull_request=pr_ref,
            committed=has_reviewable_commit,
            pushed=pushed,
            has_conflict=has_conflict,
            conflict_files=conflict_files,
            pending_review=bool((is_local_tracker or self._agent_config.review_required) and has_reviewable_commit),
        )

    def _post_commit_error(
        self,
        cause: VerificationFailed | HookFailedError,
        *,
        branch_name: str,
        base_branch: str,
        commit_sha: str | None,
        committed: bool,
        pushed: bool,
        has_conflict: bool,
        conflict_files: tuple[str, ...],
        pull_request: PullRequestRef | None,
        is_local_tracker: bool,
    ) -> GitSyncPostCommitError:
        result = GitSyncResult(
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pull_request=pull_request,
            committed=committed,
            pushed=pushed,
            has_conflict=has_conflict,
            conflict_files=conflict_files,
            pending_review=bool((is_local_tracker or self._agent_config.review_required) and committed),
        )
        return GitSyncPostCommitError(cause, result)

    async def _run_pre_commit_hook(self, repo_root: str, session: Any) -> None:
        command = self._hooks_config.pre_commit
        if not command:
            return
        output = await self._run_shell(command, repo_root, self._hooks_config.timeout_ms)
        if get_file_status(repo_root) and getattr(session, "workspace_strategy", "isolated") != "sequential":
            self._run_git_checked(["add", "-A"], repo_root)
            self._run_git_checked(["commit", "--amend", "--no-edit"], repo_root)
        setattr(session, "pre_commit_output", output)

    async def _run_pre_push_verification(self, repo_root: str, session: Any) -> None:
        outputs: list[str] = []
        for label, command in (
            ("test", self._agent_config.test_command),
            ("build", self._agent_config.build_command),
            ("lint", self._agent_config.lint_command),
        ):
            if not command:
                continue
            try:
                output = await self._run_shell(
                    command,
                    repo_root,
                    self._agent_config.verification.timeout_ms,
                )
            except VerificationFailed as exc:
                raise VerificationFailed(f"{label} verification failed", exc.output) from exc
            outputs.append(f"## {label}\n{output}".strip())
        hook_command = self._hooks_config.pre_push
        if hook_command:
            before = self._status_snapshot(repo_root)
            try:
                output = await self._run_shell(
                    hook_command,
                    repo_root,
                    self._hooks_config.timeout_ms,
                )
            except VerificationFailed as exc:
                raise HookFailedError("pre_push", "pre_push hook failed", exc.output) from exc
            if self._status_snapshot(repo_root) != before:
                raise HookFailedError(
                    "pre_push",
                    "pre_push hook modified the workspace",
                    output,
                )
            outputs.append(f"## pre_push\n{output}".strip())
        setattr(session, "verification_status", "passed")
        setattr(session, "verification_output", "\n\n".join(outputs))

    async def _run_post_sync_hook(self, repo_root: str, session: Any) -> None:
        command = self._hooks_config.post_sync
        if not command:
            return
        before = self._status_snapshot(repo_root)
        try:
            output = await self._run_shell(command, repo_root, self._hooks_config.timeout_ms)
        except VerificationFailed as exc:
            raise HookFailedError("post_sync", "post_sync hook failed", exc.output) from exc
        if self._status_snapshot(repo_root) != before:
            raise HookFailedError(
                "post_sync",
                "post_sync hook modified the workspace",
                output,
            )
        setattr(session, "post_sync_output", output)

    async def _run_shell(self, command: str, repo_root: str, timeout_ms: int) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError as exc:
            raise VerificationFailed(
                f"command timed out after {timeout_ms}ms: {command}",
                "",
            ) from exc
        output = "\n".join(
            part.decode("utf-8", errors="replace").strip()
            for part in (stdout, stderr)
            if part
        ).strip()
        if proc.returncode != 0:
            raise VerificationFailed(
                f"command failed with exit code {proc.returncode}: {command}",
                output,
            )
        return output

    def _status_snapshot(self, repo_root: str) -> str:
        return "\n".join(sorted(s.path for s in get_file_status(repo_root)))

    def _sync_gitignore(self, repo_root: str) -> None:
        gitignore_path = Path(repo_root) / ".gitignore"
        self._append_ignore_patterns(gitignore_path)

    def _sync_git_exclude(self, repo_root: str) -> None:
        exclude_path = Path(repo_root) / ".git" / "info" / "exclude"
        self._append_ignore_patterns(exclude_path)

    def _append_ignore_patterns(self, path: Path) -> None:
        existing: set[str] = set()
        if path.exists():
            existing = {
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }
        new_patterns = [
            pattern for pattern in self._gitignore_patterns if pattern not in existing
        ]
        if not new_patterns:
            return
        with path.open("a", encoding="utf-8") as handle:
            if path.exists() and path.stat().st_size > 0:
                handle.write("\n")
            handle.write("# ClawCodeX managed — do not edit manually\n")
            for pattern in new_patterns:
                handle.write(f"{pattern}\n")

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
        *,
        session: Any,
        pull_request: PullRequestRef | None,
    ) -> str:
        report_path = getattr(session, "report_path", None)
        verification_status = getattr(session, "verification_status", None) or "skipped"
        lines = [
            "## ClawCodex Automated Change",
            "",
            f"- Issue: {issue.identifier or issue.id or 'unknown'}",
            f"- Branch: `{branch_name}`",
            f"- Base: `{base_branch}`",
            f"- Commit: `{commit_sha or 'n/a'}`",
            f"- Verification: `{verification_status}`",
            f"- Report: `{report_path or 'n/a'}`",
        ]
        if issue.url:
            lines.append(f"- Source issue: {issue.url}")
        if pull_request and pull_request.url:
            lines.append(f"- Pull request: {pull_request.url}")
        if report_path:
            lines.extend(["", f"<!-- metadata: report_path={report_path} -->"])
        return "\n".join(lines)

    def _write_report(
        self,
        *,
        session: Any,
        branch_name: str,
        base_branch: str,
        commit_sha: str | None,
        pull_request: PullRequestRef | None,
    ) -> report_writer.ReportResult | None:
        run_id = getattr(session, "run_id", None)
        workspace = getattr(session, "workspace", None)
        issue = getattr(session, "issue", None)
        if not run_id or workspace is None or issue is None:
            return None
        result = report_writer.write(
            run_id=run_id,
            workspace_path=Path(workspace.path),
            tracker=getattr(self.tracker, "platform", self.tracker.__class__.__name__),
            owner=getattr(self.tracker, "owner", None),
            repo=getattr(self.tracker, "repo", None),
            issue=issue,
            status=getattr(session, "status", "unknown"),
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pr_number=str(pull_request.number) if pull_request and pull_request.number is not None else None,
            pr_url=pull_request.url if pull_request else None,
            turn_count=getattr(session, "turn_count", 0),
            tool_count=getattr(session, "tool_count", 0),
            verification_status=getattr(session, "verification_status", None),
            verification_output=getattr(session, "verification_output", None),
            output_text=getattr(session, "output_text", ""),
            # F-45: forward the per-tool audit log path so report_writer
            # can dual-write the NDJSON into the persistent layer.
            tool_events_path=getattr(session, "tool_events_path", None),
        )
        setattr(session, "report_path", result.persistent_markdown_path)
        return result

    async def _update_summary_comment(
        self,
        *,
        session: Any,
        branch_name: str,
        base_branch: str,
        commit_sha: str | None,
        pull_request: PullRequestRef | None,
        committed: bool,
        pushed: bool,
        report_path: str | None,
    ) -> None:
        issue = session.issue
        if not issue.id:
            return

        body = self._build_summary_comment_body(
            session=session,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pull_request=pull_request,
            committed=committed,
            pushed=pushed,
            report_path=report_path,
        )
        comment_id = getattr(session, "summary_comment_id", None)
        if comment_id:
            updated = await self.tracker.update_comment(issue.id, comment_id, body)
            if updated is not None:
                return
        created = await self.tracker.create_comment(issue.id, body)
        if created is not None and getattr(created, "id", None):
            setattr(session, "summary_comment_id", created.id)

    def _build_summary_comment_body(
        self,
        *,
        session: Any,
        branch_name: str,
        base_branch: str,
        commit_sha: str | None,
        pull_request: PullRequestRef | None,
        committed: bool,
        pushed: bool,
        report_path: str | None,
    ) -> str:
        verification_status = getattr(session, "verification_status", None) or "skipped"
        body_lines = [
            "## ClawCodex Run Summary",
            "",
            f"- Run: `{getattr(session, 'run_id', 'unknown')}`",
            f"- Status: `{getattr(session, 'status', 'unknown')}`",
            f"- Branch: `{branch_name}`",
            f"- Base: `{base_branch}`",
            f"- Committed: {'yes' if committed else 'no'}",
            f"- Pushed: {'yes' if pushed else 'no'}",
            f"- Verification: `{verification_status}`",
            f"- Report: `{report_path or 'n/a'}`",
        ]
        if commit_sha:
            body_lines.append(f"- Commit: `{commit_sha}`")
        if pull_request and pull_request.url:
            body_lines.append(f"- Pull request: {pull_request.url}")
        if report_path:
            body_lines.extend(["", f"<!-- metadata: report_path={report_path} -->"])
        return "\n".join(body_lines)

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
