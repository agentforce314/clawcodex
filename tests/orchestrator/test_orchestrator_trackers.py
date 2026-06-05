from __future__ import annotations

import argparse
import asyncio
import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx

from extensions.orchestrator import report_writer
from extensions.orchestrator.cli.issue import _run_review
from extensions.orchestrator.issue_registry import IssueRegistry
from extensions.orchestrator.prompt_builder import PromptBuilder
from extensions.orchestrator.review_feedback import ReviewFeedbackService
from src.orchestrator.agent_runner import AgentSession
from src.orchestrator.config.schema import WorkflowConfig
from src.orchestrator.issue import Issue
from src.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.repo_tracker.adapter import RepositoryTrackerAdapter
from src.orchestrator.tracker import (
    PullRequestFeedback,
    PullRequestRef,
    TrackerAdapter,
    TrackerConfigError,
    create_tracker_adapter,
    repository_clone_url_for_tracker,
    validate_tracker_config,
)
from src.orchestrator.workspace import Workspace, WorkspaceManager


@contextmanager
def _patched_env(values: dict[str, str]):
    original = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_issue(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


class TestWorkflowTrackerConfig(unittest.TestCase):
    def test_github_tracker_reads_kind_specific_env_defaults(self) -> None:
        with _patched_env(
            {
                "GITHUB_TOKEN": "gh-test-token",
                "GITHUB_OWNER": "acme",
                "GITHUB_REPO": "widget",
                "GITHUB_ASSIGNEE": "codex-bot",
            }
        ):
            config = WorkflowConfig.from_dict(
                {"tracker": {"kind": "github"}}
            )

        self.assertEqual(config.tracker.kind, "github")
        self.assertEqual(config.tracker.endpoint, "https://api.github.com")
        self.assertEqual(config.tracker.api_key, "gh-test-token")
        self.assertEqual(config.tracker.owner, "acme")
        self.assertEqual(config.tracker.repo, "widget")
        self.assertEqual(config.tracker.assignee, "codex-bot")
        self.assertEqual(config.tracker.active_states, ["open"])
        self.assertEqual(config.tracker.terminal_states, ["closed"])

    def test_gitcode_tracker_uses_opened_default_state(self) -> None:
        config = WorkflowConfig.from_dict({"tracker": {"kind": "gitcode"}})
        self.assertEqual(config.tracker.active_states, ["opened"])
        self.assertEqual(config.tracker.endpoint, "https://api.gitcode.com/api/v5")

    def test_review_feedback_config_defaults_to_manual_disabled(self) -> None:
        config = WorkflowConfig.from_dict({})

        self.assertFalse(config.review_feedback.enabled)
        self.assertEqual(config.review_feedback.mode, "manual")
        self.assertTrue(config.review_feedback.include_ci_failures)
        self.assertTrue(config.review_feedback.reply_to_comments)
        self.assertEqual(config.review_feedback.max_feedback_items_per_run, 20)
        self.assertEqual(config.review_feedback.max_followup_attempts_per_pr, 5)

    def test_review_feedback_config_parses_workflow_values(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "review_feedback": {
                    "enabled": True,
                    "mode": "AUTO",
                    "poll_interval_ms": 5_000,
                    "max_feedback_items_per_run": 3,
                    "include_ci_failures": False,
                    "reply_to_comments": False,
                    "ignore_authors": "clawcodex-bot",
                    "max_log_chars_per_check": 800,
                    "max_followup_attempts_per_pr": 2,
                }
            }
        )

        self.assertTrue(config.review_feedback.enabled)
        self.assertEqual(config.review_feedback.mode, "auto")
        self.assertEqual(config.review_feedback.poll_interval_ms, 5_000)
        self.assertEqual(config.review_feedback.max_feedback_items_per_run, 3)
        self.assertFalse(config.review_feedback.include_ci_failures)
        self.assertFalse(config.review_feedback.reply_to_comments)
        self.assertEqual(config.review_feedback.ignore_authors, ["clawcodex-bot"])
        self.assertEqual(config.review_feedback.max_log_chars_per_check, 800)
        self.assertEqual(config.review_feedback.max_followup_attempts_per_pr, 2)

    def test_validate_tracker_config_requires_repository_for_repo_trackers(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "github",
                    "api_key": "gh-test-token",
                }
            }
        )

        with self.assertRaises(TrackerConfigError):
            validate_tracker_config(config.tracker)

    def test_create_tracker_adapter_returns_repository_adapter(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "github",
                    "api_key": "gh-test-token",
                    "owner": "acme",
                    "repo": "widget",
                    "active_states": ["In Progress"],
                }
            }
        )

        adapter = create_tracker_adapter(config.tracker)

        self.assertIsInstance(adapter, RepositoryTrackerAdapter)
        self.assertEqual(adapter.platform, "github")
        self.assertEqual(adapter.active_states, ["In Progress"])

    def test_repository_clone_url_defaults_from_tracker_kind(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "gitee",
                    "api_key": "gitee-token",
                    "owner": "acme",
                    "repo": "widget",
                }
            }
        )

        clone_url = repository_clone_url_for_tracker(config.tracker)

        self.assertEqual(clone_url, "https://gitee.com/acme/widget.git")

    def test_local_tracker_config_uses_local_defaults(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "~/issues"}}
        )

        self.assertEqual(config.tracker.kind, "local")
        self.assertEqual(config.tracker.issues_path, os.path.expanduser("~/issues"))
        self.assertEqual(config.tracker.active_states, ["open", "ready"])
        self.assertEqual(
            config.tracker.terminal_states,
            ["completed", "closed", "cancelled", "failed", "abandoned"],
        )

    def test_agent_verification_and_sync_hooks_parse_from_workflow(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {"kind": "local", "issues_path": "/tmp/issues"},
                "agent": {
                    "test_command": "pytest",
                    "build_command": "python -m build",
                    "lint_command": "ruff check .",
                    "verification": {"timeout_ms": 123_000},
                },
                "hooks": {
                    "pre_commit": "python -m black .",
                    "pre_push": "pytest -q",
                    "post_sync": "python scripts/report.py",
                    "timeout_ms": 9_000,
                },
            }
        )

        self.assertEqual(config.agent.test_command, "pytest")
        self.assertEqual(config.agent.build_command, "python -m build")
        self.assertEqual(config.agent.lint_command, "ruff check .")
        self.assertEqual(config.agent.verification.timeout_ms, 123_000)
        self.assertEqual(config.hooks.pre_commit, "python -m black .")
        self.assertEqual(config.hooks.pre_push, "pytest -q")
        self.assertEqual(config.hooks.post_sync, "python scripts/report.py")
        self.assertEqual(config.hooks.timeout_ms, 9_000)

    def test_agent_verification_commands_default_to_empty_skip_values(self) -> None:
        config = WorkflowConfig.from_dict({})

        self.assertEqual(config.agent.test_command, "")
        self.assertEqual(config.agent.build_command, "")
        self.assertEqual(config.agent.lint_command, "")
        self.assertEqual(config.agent.verification.timeout_ms, 600_000)

    def test_tracker_state_lists_accept_scalar_values(self) -> None:
        config = WorkflowConfig.from_dict(
            {
                "tracker": {
                    "kind": "local",
                    "issues_path": "/tmp/issues",
                    "active_states": "open",
                    "terminal_states": "completed",
                }
            }
        )

        self.assertEqual(config.tracker.active_states, ["open"])
        self.assertEqual(config.tracker.terminal_states, ["completed"])

    def test_validate_local_tracker_requires_issues_path_not_api_key(self) -> None:
        valid = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )
        validate_tracker_config(valid.tracker)

        invalid = WorkflowConfig.from_dict({"tracker": {"kind": "local"}})
        with self.assertRaises(TrackerConfigError):
            validate_tracker_config(invalid.tracker)

    def test_create_tracker_adapter_returns_local_adapter(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )

        adapter = create_tracker_adapter(config.tracker)

        self.assertIsInstance(adapter, LocalTrackerAdapter)
        self.assertEqual(adapter.active_states, ["open", "ready"])

    def test_repository_clone_url_is_none_for_local_tracker(self) -> None:
        config = WorkflowConfig.from_dict(
            {"tracker": {"kind": "local", "issues_path": "/tmp/issues"}}
        )

        self.assertIsNone(repository_clone_url_for_tracker(config.tracker))


class TestLocalTrackerAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_issues_are_filtered_and_sorted(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "ready.md",
                """---
id: LOCAL-002
identifier: LOCAL-002
state: ready
priority: 2
labels:
  - orchestrator
---
# Ready issue

Do this second.
""",
            )
            _write_issue(
                issues_path / "open.md",
                """---
id: LOCAL-001
identifier: LOCAL-001
state: open
priority: 1
---
# Open issue

Do this first.
""",
            )
            _write_issue(
                issues_path / "done.md",
                """---
id: LOCAL-003
identifier: LOCAL-003
state: completed
priority: 0
---
# Done issue
""",
            )

            adapter = LocalTrackerAdapter(issues_path)
            issues = await adapter.fetch_candidate_issues()

        self.assertEqual([issue.id for issue in issues], ["LOCAL-001", "LOCAL-002"])
        self.assertEqual(issues[0].title, "Open issue")
        self.assertEqual(issues[0].description, "Do this first.")
        self.assertEqual(issues[0].branch_name, "local/local-001-open-issue")
        self.assertEqual(issues[0].depends_on, [])
        self.assertEqual(issues[1].labels, ["orchestrator"])

    async def test_markdown_issue_parses_depends_on_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "dependent.md",
                """---
id: LOCAL-002
identifier: LOCAL-002
state: open
depends_on:
  - LOCAL-001
  - LOCAL-003
---
# Dependent issue
""",
            )

            adapter = LocalTrackerAdapter(issues_path)
            issues = await adapter.fetch_candidate_issues()

        self.assertEqual(issues[0].depends_on, ["LOCAL-001", "LOCAL-003"])

    async def test_fetch_issue_states_rereads_document(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            issue_path = issues_path / "issue.md"
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: open
---
# Test issue
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            first = await adapter.fetch_issue_states_by_ids(["LOCAL-001"])
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: ready
---
# Test issue
""",
            )
            second = await adapter.fetch_issue_states_by_ids(["LOCAL-001"])

        self.assertEqual(first["LOCAL-001"].state, "open")
        self.assertEqual(second["LOCAL-001"].state, "ready")

    async def test_update_issue_state_preserves_body(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            issue_path = issues_path / "issue.md"
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
state: open
---
# Keep me

Body remains.
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            await adapter.update_issue_state("LOCAL-001", "completed")

            updated = issue_path.read_text(encoding="utf-8")

        self.assertIn("state: completed", updated)
        self.assertIn("# Keep me\n\nBody remains.", updated)
        self.assertIn("updated_at:", updated)

    async def test_comments_round_trip_through_ndjson(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(Path(tmp))

            await adapter.create_comment("LOCAL-001", "sync complete")
            clarification = await adapter.create_clarification_comment(
                "LOCAL-001",
                "Need details",
                mentions=["alice"],
            )
            comments = await adapter.fetch_issue_comments("LOCAL-001")
            new_comments = await adapter.fetch_new_comments_since(
                "LOCAL-001",
                comments[0].id,
            )

        self.assertIsNotNone(clarification)
        self.assertEqual([comment.author_login for comment in comments], ["clawcodex", "clawcodex"])
        self.assertEqual(comments[0].body, "sync complete")
        self.assertEqual(comments[1].body, "@alice\n\nNeed details")
        self.assertEqual(new_comments, [comments[1]])

    async def test_update_comment_rewrites_matching_ndjson_record(self) -> None:
        with TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(Path(tmp))
            first = await adapter.create_comment("LOCAL-001", "in progress")
            second = await adapter.create_comment("LOCAL-001", "unchanged")
            assert first is not None
            assert second is not None

            updated = await adapter.update_comment("LOCAL-001", first.id or "", "sync complete")
            comments = await adapter.fetch_issue_comments("LOCAL-001")
            tmp_files = list(Path(tmp).glob("*.tmp"))

        assert updated is not None
        self.assertEqual(updated.id, first.id)
        self.assertEqual(updated.body, "sync complete")
        self.assertEqual(comments[0].body, "sync complete")
        self.assertEqual(comments[1].body, "unchanged")
        self.assertEqual(tmp_files, [])

    async def test_comment_files_include_hash_to_avoid_sanitized_name_collision(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            adapter = LocalTrackerAdapter(issues_path)

            await adapter.create_comment("LOCAL/001", "first")
            await adapter.create_comment("LOCAL:001", "second")

            comment_files = sorted(issues_path.glob("*.comments.ndjson"))

        self.assertEqual(len(comment_files), 2)

    async def test_adapter_state_lists_are_returned_as_copies(self) -> None:
        adapter = LocalTrackerAdapter("/tmp/issues")

        active_states = adapter.active_states
        active_states.append("mutated")

        self.assertEqual(adapter.active_states, ["open", "ready"])

    async def test_find_pull_request_skips_matching_document_without_pr_url(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "without-pr.md",
                """---
id: LOCAL-001
state: open
branch_name: local/branch
base_branch: main
---
# Missing PR URL
""",
            )
            _write_issue(
                issues_path / "with-pr.md",
                """---
id: LOCAL-002
state: open
branch_name: local/branch
base_branch: main
pr_number: '43'
pr_url: https://example.invalid/pr/43
pr_title: Complete PR
---
# Complete PR
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            pr = await adapter.find_pull_request(
                head_branch="local/branch",
                base_branch="main",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="43",
                url="https://example.invalid/pr/43",
                title="Complete PR",
            ),
        )

    async def test_find_pull_request_uses_local_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            issues_path = Path(tmp)
            _write_issue(
                issues_path / "issue.md",
                """---
id: LOCAL-001
state: open
branch_name: local/branch
base_branch: main
pr_number: '42'
pr_url: https://example.invalid/pr/42
pr_title: Local PR
---
# Test issue
""",
            )
            adapter = LocalTrackerAdapter(issues_path)

            pr = await adapter.find_pull_request(
                head_branch="local/branch",
                base_branch="main",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="42",
                url="https://example.invalid/pr/42",
                title="Local PR",
            ),
        )


class TestIssueReviewCli(unittest.TestCase):
    def test_review_approve_syncs_local_issue_state_from_workflow(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            issues_path = root / "issues"
            issues_path.mkdir()
            issue_path = issues_path / "issue.md"
            _write_issue(
                issue_path,
                """---
id: LOCAL-001
identifier: LOCAL-001
state: pending_review
---
# Review me
""",
            )
            workflow_path = root / "workflow.md"
            workflow_path.write_text(
                f"""---
tracker:
  kind: local
  issues_path: {issues_path}
---
Run the issue.
""",
                encoding="utf-8",
            )
            registry_path = root / "registry.json"
            registry = IssueRegistry(registry_path)
            registry.register("LOCAL-001", "LOCAL-001")
            registry.mark_pending_review("LOCAL-001")
            args = argparse.Namespace(
                id="LOCAL-001",
                approve=True,
                reject=False,
                feedback=None,
                comment="Looks good",
                workflow=str(workflow_path),
                workspace=str(root),
            )

            rc = _run_review(registry_path, args)
            updated = issue_path.read_text(encoding="utf-8")

        self.assertEqual(rc, 0)
        self.assertIn("state: completed", updated)
        self.assertIn("updated_at:", updated)


class TestReportWriter(unittest.TestCase):
    def test_write_creates_workspace_and_persistent_markdown_json(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            home = Path(tmp) / "home"
            workspace.mkdir()
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                result = report_writer.write(
                    run_id="run-01-20260601T000000Z",
                    workspace_path=workspace,
                    tracker="github",
                    owner="acme",
                    repo="widget",
                    issue=Issue(id="77", identifier="ISSUE-77", title="Verify reports"),
                    status="completed",
                    branch_name="clawcodex/issue-77",
                    base_branch="main",
                    commit_sha="abc123",
                    pr_number="9",
                    pr_url="https://example.test/pr/9",
                    turn_count=2,
                    tool_count=3,
                    verification_status="passed",
                    verification_output="pytest passed",
                    output_text="agent output",
                )
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

            workspace_md = Path(result.workspace_markdown_path)
            persistent_md = Path(result.persistent_markdown_path)
            workspace_json = Path(result.workspace_json_path)
            persistent_json = Path(result.persistent_json_path)
            markdown = workspace_md.read_text(encoding="utf-8")
            persistent_markdown = persistent_md.read_text(encoding="utf-8")
            workspace_payload = workspace_json.read_text(encoding="utf-8")
            persistent_payload = persistent_json.read_text(encoding="utf-8")
            payload = json.loads(workspace_payload)

            self.assertTrue(workspace_md.exists())
            self.assertTrue(persistent_md.exists())
            self.assertTrue(workspace_json.exists())
            self.assertTrue(persistent_json.exists())
            self.assertEqual(markdown, persistent_markdown)
            self.assertEqual(workspace_payload, persistent_payload)
            self.assertIn("# ClawCodex Run Report", markdown)
            self.assertIn("- Verification: `passed`", markdown)
            self.assertNotIn(result.workspace_markdown_path, markdown)
            self.assertNotIn(result.persistent_markdown_path, markdown)
            self.assertEqual(payload["run_id"], "run-01-20260601T000000Z")
            self.assertEqual(payload["verification_output"], "pytest passed")


class TestReviewFeedbackPrompt(unittest.TestCase):
    def test_review_feedback_prompt_constrains_followup_scope(self) -> None:
        prompt = PromptBuilder.render_review_feedback(
            issue={
                "id": "42",
                "identifier": "#42",
                "title": "Implement feature",
            },
            pull_request=PullRequestRef(number="9", url="https://example.test/pr/9"),
            branch_name="clawcodex/issue-42",
            feedback=[
                PullRequestFeedback(
                    id="inline_review:202",
                    source="inline_review",
                    body="Use the existing helper here.",
                    file_path="src/app.py",
                    line=12,
                    diff_hunk="@@ -1 +1 @@",
                    severity="warning",
                    status="open",
                )
            ],
        )

        self.assertIn("fixing pull request feedback", prompt)
        self.assertIn("Fix only the PR review feedback", prompt)
        self.assertIn("Work on the current branch only", prompt)
        self.assertIn("Pull request: #9", prompt)
        self.assertIn("Branch: clawcodex/issue-42", prompt)
        self.assertIn("File: src/app.py:12", prompt)
        self.assertIn("Use the existing helper here.", prompt)


class TestIssueRegistryFeedbackState(unittest.TestCase):
    def test_feedback_state_is_persisted_and_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            registry = IssueRegistry(registry_path)
            registry.register(
                "42",
                "#42",
                branch_name="clawcodex/issue-42",
            )
            registry.mark_synced(
                "42",
                branch_name="clawcodex/issue-42",
                pr_number="7",
                pr_url="https://example.test/pr/7",
            )

            registry.mark_feedback_pending(
                "42",
                ["conversation:1", "inline_review:2", "conversation:1"],
                cursor="cursor-1",
            )
            registry.mark_feedback_processed(
                "42",
                ["conversation:1"],
                commit_sha="abc123",
            )
            registry.increment_followup_attempt("42")

            reloaded = IssueRegistry(registry_path)
            record = reloaded.get("42")

        assert record is not None
        self.assertEqual(record.pending_feedback_ids, ["inline_review:2"])
        self.assertEqual(record.processed_feedback_ids, ["conversation:1"])
        self.assertEqual(record.feedback_cursor, "cursor-1")
        self.assertEqual(record.last_followup_commit_sha, "abc123")
        self.assertEqual(record.followup_attempt_count, 1)
        self.assertTrue(reloaded.can_follow_up("42", 2))
        self.assertFalse(reloaded.can_follow_up("42", 1))
        self.assertEqual(len(reloaded.iter_records_with_pr()), 1)

    def test_registry_load_ignores_unknown_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "42": {
                            "issue_id": "42",
                            "issue_identifier": "#42",
                            "branch_name": "clawcodex/issue-42",
                            "unknown_future_field": "ignored",
                        }
                    }
                ),
                encoding="utf-8",
            )

            registry = IssueRegistry(registry_path)
            record = registry.get("42")

        assert record is not None
        self.assertEqual(record.issue_identifier, "#42")
        self.assertEqual(record.pending_feedback_ids, [])


class _ReviewFeedbackTracker(TrackerAdapter):
    def __init__(self, feedback: list[PullRequestFeedback]) -> None:
        self.feedback = feedback
        self.fetch_requests: list[tuple[PullRequestRef, bool]] = []
        self.replies: list[tuple[PullRequestRef, PullRequestFeedback, str]] = []

    async def fetch_candidate_issues(self) -> list[Issue]:
        return []

    async def fetch_issue_states_by_ids(
        self, issue_ids: list[str]
    ) -> dict[str, Issue]:
        return {}

    async def create_comment(self, issue_id: str, body: str) -> None:
        return None

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        return None

    async def fetch_pull_request_feedback(
        self,
        *,
        pull_request: PullRequestRef,
        include_ci_failures: bool = True,
        max_log_chars_per_check: int = 12_000,
    ) -> list[PullRequestFeedback]:
        self.fetch_requests.append((pull_request, include_ci_failures))
        return list(self.feedback)

    async def reply_to_pull_request_feedback(
        self,
        *,
        pull_request: PullRequestRef,
        feedback: PullRequestFeedback,
        body: str,
    ):
        self.replies.append((pull_request, feedback, body))
        return None


class _ReviewWorkspaceManager(WorkspaceManager):
    def __init__(self, root: Path) -> None:
        super().__init__(WorkflowConfig.from_dict({"workspace": {"root": str(root)}}).workspace)
        self.created_for: list[Issue] = []

    async def create_for_issue(self, issue: Issue) -> Workspace:
        self.created_for.append(issue)
        path = Path(self.config.root) / (issue.id or "issue")
        path.mkdir(parents=True, exist_ok=True)
        return Workspace(path=path, issue_identifier=issue.identifier or "issue", issue_id=issue.id)

    async def run_before_run_hook(self, workspace: Workspace, issue: Issue) -> None:
        return None

    async def run_after_run_hook(self, workspace: Workspace, issue: Issue) -> None:
        return None

    async def cleanup(self, issue: Issue) -> None:
        return None

    async def run_terminal_workspace_cleanup(self) -> None:
        return None


class _ReviewAgentRunner:
    max_turns = 2

    async def run(self, session: AgentSession, workflow: WorkflowConfig, **kwargs) -> None:
        session.status = "completed"


class _ReviewOrchestrator(Orchestrator):
    async def _run_issue(self, session: AgentSession) -> None:
        return None


class _DependencyTracker(TrackerAdapter):
    active_states = ["open"]

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        self.updated_states: list[tuple[str, str]] = []

    async def fetch_candidate_issues(self) -> list[Issue]:
        return list(self.issues)

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> dict[str, Issue]:
        return {issue.id or "": issue for issue in self.issues if issue.id in issue_ids}

    async def create_comment(self, issue_id: str, body: str) -> None:
        return None

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        self.updated_states.append((issue_id, state))


class TestOrchestratorDependencies(unittest.IsolatedAsyncioTestCase):
    async def test_poll_skips_issue_until_dependencies_are_completed(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _DependencyTracker(
                [
                    Issue(id="child", identifier="LOCAL-002", state="open", depends_on=["parent"]),
                    Issue(id="independent", identifier="LOCAL-003", state="open"),
                ]
            )
            orchestrator = _ReviewOrchestrator(
                workflow=WorkflowConfig.from_dict(
                    {
                        "workspace": {"root": tmp},
                        "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                    }
                ),
                tracker=tracker,
                workspace=_ReviewWorkspaceManager(Path(tmp)),
                agent_runner=_ReviewAgentRunner(),
            )

            await orchestrator._poll_and_dispatch()

            self.assertNotIn("child", orchestrator._state.running)
            self.assertIn("independent", orchestrator._state.running)

    async def test_poll_launches_issue_after_dependencies_complete(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _DependencyTracker(
                [Issue(id="child", identifier="LOCAL-002", state="open", depends_on=["parent"])]
            )
            orchestrator = _ReviewOrchestrator(
                workflow=WorkflowConfig.from_dict(
                    {
                        "workspace": {"root": tmp},
                        "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                    }
                ),
                tracker=tracker,
                workspace=_ReviewWorkspaceManager(Path(tmp)),
                agent_runner=_ReviewAgentRunner(),
            )
            orchestrator._registry.register("parent", "LOCAL-001")
            orchestrator._registry.mark_completed("parent")

            await orchestrator._poll_and_dispatch()

            self.assertIn("child", orchestrator._state.running)

    async def test_poll_skips_terminal_registry_issue_still_active_in_tracker(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _DependencyTracker(
                [Issue(id="done", identifier="LOCAL-001", state="open")]
            )
            orchestrator = _ReviewOrchestrator(
                workflow=WorkflowConfig.from_dict(
                    {
                        "workspace": {"root": tmp},
                        "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                    }
                ),
                tracker=tracker,
                workspace=_ReviewWorkspaceManager(Path(tmp)),
                agent_runner=_ReviewAgentRunner(),
            )
            orchestrator._registry.register("done", "LOCAL-001")
            orchestrator._registry.mark_abandoned("done")

            await orchestrator._poll_and_dispatch()

            self.assertNotIn("done", orchestrator._state.running)
            self.assertEqual(orchestrator.workspace.created_for, [])

    async def test_escalated_issue_syncs_terminal_tracker_state(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _DependencyTracker([])
            orchestrator = _ReviewOrchestrator(
                workflow=WorkflowConfig.from_dict(
                    {
                        "workspace": {"root": tmp},
                        "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                    }
                ),
                tracker=tracker,
                workspace=_ReviewWorkspaceManager(Path(tmp)),
                agent_runner=_ReviewAgentRunner(),
            )
            orchestrator._registry.register("blocked", "LOCAL-001")
            sentinel_path = Path(tmp) / ".escalated_issues.json"
            sentinel_path.write_text(json.dumps({"blocked": {}}, indent=2), encoding="utf-8")

            await orchestrator._process_escalated_issues()

            self.assertEqual(tracker.updated_states, [("blocked", "abandoned")])

    async def test_recover_stale_running_syncs_tracker_state(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _DependencyTracker([])
            orchestrator = _ReviewOrchestrator(
                workflow=WorkflowConfig.from_dict(
                    {
                        "workspace": {"root": tmp},
                        "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                    }
                ),
                tracker=tracker,
                workspace=_ReviewWorkspaceManager(Path(tmp)),
                agent_runner=_ReviewAgentRunner(),
            )
            orchestrator._registry.register("stale", "LOCAL-001")
            orchestrator._registry.mark_running("stale")

            await orchestrator._recover_stale_running_records()

            self.assertEqual(tracker.updated_states, [("stale", "failed")])


class TestReviewFeedbackService(unittest.IsolatedAsyncioTestCase):
    def _registry_with_pr(self, path: Path) -> IssueRegistry:
        registry = IssueRegistry(path)
        registry.register("42", "#42", branch_name="clawcodex/issue-42")
        registry.mark_synced(
            "42",
            branch_name="clawcodex/issue-42",
            pr_number="9",
            pr_url="https://example.test/pr/9",
        )
        return registry

    async def test_collect_followups_filters_processed_ignored_resolved_and_outdated(self) -> None:
        with TemporaryDirectory() as tmp:
            registry = self._registry_with_pr(Path(tmp) / "registry.json")
            registry.mark_feedback_processed("42", ["conversation:processed"])
            config = WorkflowConfig.from_dict(
                {
                    "review_feedback": {
                        "enabled": True,
                        "ignore_authors": ["clawcodex-bot"],
                    }
                }
            ).review_feedback
            tracker = _ReviewFeedbackTracker(
                [
                    PullRequestFeedback(
                        id="conversation:processed",
                        source="conversation",
                        body="already done",
                    ),
                    PullRequestFeedback(
                        id="conversation:ignored",
                        source="conversation",
                        body="bot comment",
                        author_login="clawcodex-bot",
                    ),
                    PullRequestFeedback(
                        id="inline_review:resolved",
                        source="inline_review",
                        body="resolved",
                        status="resolved",
                    ),
                    PullRequestFeedback(
                        id="inline_review:outdated",
                        source="inline_review",
                        body="outdated",
                        status="outdated",
                    ),
                    PullRequestFeedback(
                        id="conversation:new",
                        source="conversation",
                        body="please fix this",
                        updated_at="cursor-new",
                    ),
                ]
            )

            followups = await ReviewFeedbackService(
                tracker=tracker,
                registry=registry,
                config=config,
            ).collect_followups(available_slots=1)
            reloaded = IssueRegistry(Path(tmp) / "registry.json")
            record = reloaded.get("42")

        self.assertEqual(len(followups), 1)
        self.assertEqual([item.id for item in followups[0].feedback], ["conversation:new"])
        assert record is not None
        self.assertEqual(record.pending_feedback_ids, ["conversation:new"])
        self.assertEqual(record.feedback_cursor, "cursor-new")

    async def test_collect_followups_skips_feedback_already_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            registry = self._registry_with_pr(registry_path)
            config = WorkflowConfig.from_dict(
                {"review_feedback": {"enabled": True}}
            ).review_feedback
            tracker = _ReviewFeedbackTracker(
                [
                    PullRequestFeedback(
                        id="conversation:dupe",
                        source="conversation",
                        body="please fix once",
                    )
                ]
            )
            service = ReviewFeedbackService(
                tracker=tracker,
                registry=registry,
                config=config,
            )

            first = await service.collect_followups(available_slots=1)
            second = await service.collect_followups(available_slots=1)
            record = IssueRegistry(registry_path).get("42")

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        assert record is not None
        self.assertEqual(record.pending_feedback_ids, ["conversation:dupe"])
        self.assertEqual(record.processed_feedback_ids, [])

    async def test_collect_followups_persists_empty_feedback_check_time(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            registry = self._registry_with_pr(registry_path)
            config = WorkflowConfig.from_dict(
                {"review_feedback": {"enabled": True}}
            ).review_feedback
            tracker = _ReviewFeedbackTracker([])

            followups = await ReviewFeedbackService(
                tracker=tracker,
                registry=registry,
                config=config,
            ).collect_followups(available_slots=1)
            record = IssueRegistry(registry_path).get("42")

        self.assertEqual(followups, [])
        assert record is not None
        self.assertIsNotNone(record.last_feedback_checked_at)


class TestOrchestratorReviewFeedback(unittest.IsolatedAsyncioTestCase):
    def _workflow(self, tmp: str, mode: str) -> WorkflowConfig:
        return WorkflowConfig.from_dict(
            {
                "workspace": {"root": tmp},
                "agent": {"max_concurrent_agents": 1, "max_turns": 2},
                "review_feedback": {"enabled": True, "mode": mode},
            }
        )

    def _orchestrator(
        self,
        tmp: str,
        mode: str,
        tracker: _ReviewFeedbackTracker,
    ) -> Orchestrator:
        workflow = self._workflow(tmp, mode)
        workspace = _ReviewWorkspaceManager(Path(tmp))
        orchestrator = _ReviewOrchestrator(
            workflow=workflow,
            tracker=tracker,
            workspace=workspace,
            agent_runner=_ReviewAgentRunner(),
        )
        orchestrator._registry.register(
            "42",
            "#42",
            branch_name="clawcodex/issue-42",
        )
        orchestrator._registry.mark_synced(
            "42",
            branch_name="clawcodex/issue-42",
            pr_number="9",
            pr_url="https://example.test/pr/9",
        )
        return orchestrator

    async def asyncTearDown(self) -> None:
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            if task.get_coro().__qualname__.startswith("Orchestrator."):
                task.cancel()

    async def test_manual_mode_records_pending_feedback_without_launching(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _ReviewFeedbackTracker(
                [
                    PullRequestFeedback(
                        id="conversation:1",
                        source="conversation",
                        body="Please fix this.",
                    )
                ]
            )
            orchestrator = self._orchestrator(tmp, "manual", tracker)

            await orchestrator._process_review_feedback()
            record = orchestrator._registry.get("42")

        assert record is not None
        self.assertEqual(record.pending_feedback_ids, ["conversation:1"])
        self.assertEqual(orchestrator._state.running, {})
        self.assertEqual(orchestrator._state.claimed, set())
        self.assertEqual(len(orchestrator._tasks), 0)

    async def test_auto_mode_launches_review_followup_session(self) -> None:
        with TemporaryDirectory() as tmp:
            tracker = _ReviewFeedbackTracker(
                [
                    PullRequestFeedback(
                        id="inline_review:202",
                        source="inline_review",
                        body="Use the existing helper here.",
                        file_path="src/app.py",
                        line=12,
                    )
                ]
            )
            orchestrator = self._orchestrator(tmp, "auto", tracker)

            await orchestrator._process_review_feedback()
            session = orchestrator._state.running.get("42")
            record = orchestrator._registry.get("42")

        assert session is not None
        assert record is not None
        self.assertEqual(session.run_kind, "review_followup")
        self.assertEqual(session.pull_request, PullRequestRef(number="9", url="https://example.test/pr/9"))
        self.assertEqual(session.feedback_ids, ["inline_review:202"])
        self.assertIn("Fix only the PR review feedback", session.prompt_override or "")
        self.assertIn("Use the existing helper here.", session.prompt_override or "")
        self.assertEqual(record.pending_feedback_ids, ["inline_review:202"])
        self.assertEqual(record.followup_attempt_count, 1)
        self.assertEqual(orchestrator._state.claimed, {"42"})
        self.assertEqual(len(orchestrator._tasks), 1)


class TestRepositoryTrackerAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_github_candidate_fetch_normalizes_and_filters_issues(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/repos/acme/widget/issues":
                payload = [
                    {
                        "number": 12,
                        "title": "Fix failing build",
                        "body": "details",
                        "state": "open",
                        "labels": [{"name": "In Progress"}],
                        "assignee": {"login": "codex-bot"},
                        "html_url": "https://github.com/acme/widget/issues/12",
                    },
                    {
                        "number": 13,
                        "title": "PR masquerading as issue",
                        "state": "open",
                        "pull_request": {"url": "https://api.github.com/repos/acme/widget/pulls/13"},
                    },
                    {
                        "number": 14,
                        "title": "Assigned elsewhere",
                        "state": "open",
                        "labels": [{"name": "In Progress"}],
                        "assignee": {"login": "someone-else"},
                    },
                ]
                return httpx.Response(200, json=payload)
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                active_states=["In Progress"],
                assignee="codex-bot",
                http_client=client,
            )

            issues = await adapter.fetch_candidate_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].id, "12")
        self.assertEqual(issues[0].identifier, "#12")
        self.assertEqual(issues[0].state, "in progress")
        self.assertEqual(issues[0].labels, ["in progress"])
        self.assertEqual(issues[0].assignee_id, "codex-bot")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer gh-test-token")

    async def test_github_issue_branch_is_extracted_from_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 15,
                        "title": "Fix branch workflow",
                        "body": "Branch: feature/issue-15\n\nDo the work.",
                        "state": "open",
                    }
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            issues = await adapter.fetch_candidate_issues()

        self.assertEqual(issues[0].branch_name, "feature/issue-15")

    async def test_gitee_comment_uses_access_token_query_param(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["query"] = dict(request.url.params)
            body = request.content.decode("utf-8")
            seen["body"] = body
            return httpx.Response(201, json={"id": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="gitee",
                owner="acme",
                repo="widget",
                api_key="gitee-token",
                http_client=client,
            )
            await adapter.create_comment("99", "job finished")

        self.assertEqual(
            seen["path"],
            "/api/v5/repos/acme/widget/issues/99/comments",
        )
        self.assertEqual(seen["query"]["access_token"], "gitee-token")
        self.assertIn("body=job+finished", seen["body"])

    async def test_github_update_comment_uses_issue_comment_patch(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "body": "updated summary",
                    "user": {"login": "clawcodex"},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            comment = await adapter.update_comment("99", "123", "updated summary")

        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(seen["path"], "/repos/acme/widget/issues/comments/123")
        self.assertEqual(seen["payload"], {"body": "updated summary"})
        assert comment is not None
        self.assertEqual(comment.id, "123")
        self.assertEqual(comment.body, "updated summary")

    async def test_gitee_update_comment_uses_access_token_form_patch(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["query"] = dict(request.url.params)
            seen["body"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id": 321, "body": "updated"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="gitee",
                owner="acme",
                repo="widget",
                api_key="gitee-token",
                http_client=client,
            )
            await adapter.update_comment("99", "321", "updated")

        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(seen["path"], "/api/v5/repos/acme/widget/issues/comments/321")
        self.assertEqual(seen["query"]["access_token"], "gitee-token")
        self.assertIn("body=updated", seen["body"])

    async def test_github_refresh_by_ids_returns_mapping(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            issue_no = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "number": int(issue_no),
                    "title": f"Issue {issue_no}",
                    "state": "open",
                    "labels": [{"name": "Todo"}],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                active_states=["Todo"],
                http_client=client,
            )
            issues = await adapter.fetch_issue_states_by_ids(["7", "8"])

        self.assertEqual(sorted(issues), ["7", "8"])
        self.assertEqual(issues["7"].state, "todo")

    async def test_ensure_pull_request_uses_existing_open_pr(self) -> None:
        seen_requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append((request.method, request.url.path))
            if request.method == "GET" and request.url.path == "/repos/acme/widget/pulls":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 21,
                            "title": "Existing PR",
                            "html_url": "https://github.com/acme/widget/pull/21",
                        }
                    ],
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            pr = await adapter.ensure_pull_request(
                issue=None,  # type: ignore[arg-type]
                head_branch="feature/issue-1",
                base_branch="main",
                title="PR title",
                body="PR body",
            )

        self.assertEqual(
            pr,
            PullRequestRef(
                number="21",
                title="Existing PR",
                url="https://github.com/acme/widget/pull/21",
            ),
        )
        self.assertEqual(seen_requests, [("GET", "/repos/acme/widget/pulls")])

    async def test_ensure_pull_request_creates_when_missing(self) -> None:
        seen_payloads: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/repos/acme/widget/pulls":
                return httpx.Response(200, json=[])
            if request.method == "POST" and request.url.path == "/repos/acme/widget/pulls":
                seen_payloads.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    201,
                    json={
                        "number": 22,
                        "title": "Created PR",
                        "html_url": "https://github.com/acme/widget/pull/22",
                    },
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            pr = await adapter.ensure_pull_request(
                issue=None,  # type: ignore[arg-type]
                head_branch="feature/issue-2",
                base_branch="main",
                title="PR title",
                body="PR body",
            )

        self.assertEqual(pr.number, "22")
        self.assertEqual(
            seen_payloads[0],
            {
                "title": "PR title",
                "head": "feature/issue-2",
                "base": "main",
                "body": "PR body",
            },
        )

    async def test_update_pull_request_uses_pull_patch(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "number": 22,
                    "title": "Updated PR",
                    "html_url": "https://github.com/acme/widget/pull/22",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            pr = await adapter.update_pull_request(
                pull_request=PullRequestRef(number="22", title="Old PR"),
                title="Updated PR",
                body="updated body",
            )

        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(seen["path"], "/repos/acme/widget/pulls/22")
        self.assertEqual(seen["payload"], {"title": "Updated PR", "body": "updated body"})
        self.assertEqual(
            pr,
            PullRequestRef(
                number="22",
                title="Updated PR",
                url="https://github.com/acme/widget/pull/22",
            ),
        )

    async def test_gitcode_fetch_pull_request_feedback_normalizes_comments_and_ci(self) -> None:
        requests: list[httpx.Request] = []
        long_summary = "x" * 40

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.url.params.get("access_token"), "gitcode-token")
            if request.method == "GET" and request.url.path == "/api/v5/repos/acme/widget/issues/9/comments":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 101,
                            "body": "Please update docs",
                            "user": {"login": "reviewer"},
                            "html_url": "https://gitcode.test/comment/101",
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
            if request.method == "GET" and request.url.path == "/api/v5/repos/acme/widget/pulls/9/comments":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 202,
                            "body": "This branch is wrong",
                            "user": {"login": "reviewer"},
                            "path": "src/app.py",
                            "line": 12,
                            "diff_hunk": "@@ -1 +1 @@",
                            "commit_id": "headsha",
                            "outdated": False,
                        }
                    ],
                )
            if request.method == "GET" and request.url.path == "/api/v5/repos/acme/widget/pulls/9/reviews":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 303,
                            "body": "Changes requested",
                            "state": "changes_requested",
                            "user": {"login": "lead"},
                            "commit_id": "headsha",
                        }
                    ],
                )
            if request.method == "GET" and request.url.path == "/api/v5/repos/acme/widget/pulls/9":
                return httpx.Response(200, json={"head": {"sha": "headsha"}})
            if request.method == "GET" and request.url.path == "/api/v5/repos/acme/widget/commits/headsha/statuses":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "ci-1",
                            "context": "pytest",
                            "state": "failed",
                            "description": "Unit tests failed",
                            "target_url": "https://ci.test/1",
                        },
                        {
                            "id": "ci-2",
                            "context": "lint",
                            "state": "success",
                        },
                        {
                            "id": "ci-3",
                            "context": "integration",
                            "state": "error",
                            "description": long_summary,
                        },
                    ],
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="gitcode",
                owner="acme",
                repo="widget",
                api_key="gitcode-token",
                http_client=client,
            )
            feedback = await adapter.fetch_pull_request_feedback(
                pull_request=PullRequestRef(number="9"),
                max_log_chars_per_check=30,
            )

        self.assertEqual(
            [item.id for item in feedback],
            [
                "conversation:101",
                "inline_review:202",
                "review_summary:303",
                "ci:headsha:ci-1",
                "ci:headsha:ci-3",
            ],
        )
        self.assertEqual(feedback[1].file_path, "src/app.py")
        self.assertEqual(feedback[1].line, 12)
        self.assertEqual(feedback[1].status, "open")
        self.assertEqual(feedback[2].severity, "error")
        self.assertEqual(feedback[3].severity, "error")
        self.assertTrue(feedback[4].body.endswith("...<truncated>"))
        self.assertEqual(len(requests), 5)

    async def test_gitcode_reply_to_inline_feedback_strips_normalized_prefix(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["query"] = dict(request.url.params)
            seen["body"] = request.content.decode("utf-8")
            return httpx.Response(
                201,
                json={
                    "id": 404,
                    "body": "Handled",
                    "user": {"login": "clawcodex"},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="gitcode",
                owner="acme",
                repo="widget",
                api_key="gitcode-token",
                http_client=client,
            )
            comment = await adapter.reply_to_pull_request_feedback(
                pull_request=PullRequestRef(number="9"),
                feedback=PullRequestFeedback(
                    id="inline_review:202",
                    source="inline_review",
                    body="Fix this",
                ),
                body="Handled",
            )

        self.assertEqual(
            seen["path"],
            "/api/v5/repos/acme/widget/pulls/9/comments/202/replies",
        )
        self.assertEqual(seen["query"]["access_token"], "gitcode-token")
        self.assertIn("body=Handled", seen["body"])
        assert comment is not None
        self.assertEqual(comment.in_reply_to_id, "inline_review:202")

    async def test_conversation_feedback_reply_posts_pr_issue_comment(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(201, json={"id": 505, "body": "Handled"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RepositoryTrackerAdapter(
                platform="github",
                owner="acme",
                repo="widget",
                api_key="gh-test-token",
                http_client=client,
            )
            await adapter.reply_to_pull_request_feedback(
                pull_request=PullRequestRef(number="9"),
                feedback=PullRequestFeedback(
                    id="conversation:101",
                    source="conversation",
                    body="Please update docs",
                ),
                body="Handled",
            )

        self.assertEqual(seen["path"], "/repos/acme/widget/issues/9/comments")
        self.assertEqual(seen["payload"], {"body": "Handled"})
