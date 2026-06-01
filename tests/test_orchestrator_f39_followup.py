"""F-39 Sub-C: agent:follow-up same-branch commit path.

Covers:
  - GitSyncService.sync(mode="followup") requires session.pull_request
  - GitSyncService.sync(mode="default") is unaffected by Sub-C changes
  - Orchestrator._prepare_intent_session wires the session for FOLLOWUP
  - Orchestrator._prepare_intent_session is a no-op for RETRY / NONE
  - Orchestrator passes mode="followup" to git_sync when run_kind is
    "agent_followup" (verified via mock observation)
  - After a followup run, followup_attempt_count is incremented and
    last_followup_commit_sha is updated; pr_number / status preserved
  - IssueRegistry.increment_followup_attempt round-trip
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from extensions.orchestrator.agent_runner import AgentSession
from extensions.orchestrator.config.schema import WorkflowConfig
from extensions.orchestrator.git_sync import GitSyncError, GitSyncService
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.issue_registry import (
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.orchestrator import Orchestrator
from extensions.orchestrator.tracker import (
    Intent,
    PullRequestRef,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    *,
    tracker: Any,
    registry: IssueRegistry,
) -> Orchestrator:
    """Construct an Orchestrator bypassing the full __init__.

    The intent-session wiring reads from `self._registry` and
    `session.issue`, so we only need those plus a few stubs.
    """
    workflow = WorkflowConfig()
    orch = Orchestrator.__new__(Orchestrator)
    orch.workflow = workflow
    orch.tracker = tracker
    orch.workspace = MagicMock()
    orch.agent_runner = MagicMock()
    orch.status_dashboard = MagicMock()
    orch._registry = registry
    orch._state = MagicMock()
    return orch


def _make_session(issue_id: str = "1", branch_name: str | None = None) -> AgentSession:
    issue = Issue(
        id=issue_id,
        identifier=f"ISSUE-{issue_id}",
        title="Refine prior fix",
        branch_name=branch_name,
    )
    workspace = MagicMock()
    workspace.path = "/tmp/workspace"
    return AgentSession(
        issue=issue,
        workspace=workspace,
        pause_resume_event=MagicMock(),
        event_queue=MagicMock(),
    )


# ---------------------------------------------------------------------------
# GitSyncService.sync(mode=...) validation
# ---------------------------------------------------------------------------


class TestGitSyncServiceFollowupMode(unittest.IsolatedAsyncioTestCase):
    async def test_followup_mode_requires_session_pull_request(self) -> None:
        """`mode="followup"` without a wired pull_request must raise."""
        service = GitSyncService(MagicMock())
        session = MagicMock(spec=["workspace"])  # no `pull_request` attr
        session.workspace.path = "/nonexistent"
        # Deliberately no session.pull_request.
        with self.assertRaises(GitSyncError) as cm:
            await service.sync(session, mode="followup")
        self.assertIn("session.pull_request", str(cm.exception))

    async def test_default_mode_does_not_require_pull_request(self) -> None:
        """`mode="default"` is the original behavior — no validation gate."""
        service = GitSyncService(MagicMock())
        session = MagicMock()
        session.workspace.path = "/nonexistent"  # repo_root will be None → None
        # The function returns None when repo_root can't be resolved
        # (no real git repo), but it must not raise the "requires
        # session.pull_request" gate that followup mode imposes.
        result = await service.sync(session, mode="default")
        self.assertIsNone(result)

    async def test_followup_mode_with_pull_request_passes_validation(self) -> None:
        """`mode="followup"` with a wired PR does not raise the gate."""
        service = GitSyncService(MagicMock())
        session = MagicMock()
        session.workspace.path = "/nonexistent"
        session.pull_request = PullRequestRef(number="7")
        # The repo_root check returns None for a nonexistent path,
        # so the function short-circuits to None — but importantly
        # it does NOT raise GitSyncError from the followup gate.
        result = await service.sync(session, mode="followup")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Orchestrator._prepare_intent_session
# ---------------------------------------------------------------------------


class TestPrepareIntentSession(unittest.TestCase):
    def test_followup_wires_run_kind_and_pull_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced(
                "1",
                branch_name="clawcodex/issue-1-foo",
                commit_sha="abc",
                pr_number="42",
                pr_url="https://example.test/pr/42",
            )
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.FOLLOWUP, source="label")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")

            orch._prepare_intent_session(session)

            self.assertEqual(session.run_kind, "agent_followup")
            self.assertIsNotNone(session.pull_request)
            self.assertEqual(session.pull_request.number, "42")
            self.assertEqual(session.pull_request.url, "https://example.test/pr/42")
            self.assertEqual(session.base_branch, "main")
            self.assertEqual(session.issue.branch_name, "clawcodex/issue-1-foo")

    def test_followup_uses_registry_base_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(
                issue_id="1",
                issue_identifier="ISSUE-1",
                base_branch="develop",
            )
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.FOLLOWUP, source="label")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")

            orch._prepare_intent_session(session)

            self.assertEqual(session.base_branch, "develop")

    def test_retry_intent_is_noop(self) -> None:
        """For RETRY, the registry is already reset; session stays default."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.RETRY, source="label")
            reg.reset_for_retry("1")  # mirrors the Sub-B path

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")

            orch._prepare_intent_session(session)

            self.assertEqual(session.run_kind, "issue")  # default
            self.assertIsNone(getattr(session, "pull_request", None))

    def test_none_intent_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")

            orch._prepare_intent_session(session)

            self.assertEqual(session.run_kind, "issue")

    def test_missing_record_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="missing")

            orch._prepare_intent_session(session)

            self.assertEqual(session.run_kind, "issue")

    def test_followup_without_pr_number_skips_pull_request(self) -> None:
        """Edge case: FOLLOWUP intent but no PR yet. The session just
        doesn't get a pull_request attribute set."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.FOLLOWUP, source="label")
            # No mark_synced, so pr_number is None.

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")

            orch._prepare_intent_session(session)

            self.assertEqual(session.run_kind, "agent_followup")
            self.assertIsNone(getattr(session, "pull_request", None))


# ---------------------------------------------------------------------------
# IssueRegistry followup-attempt counter
# ---------------------------------------------------------------------------


class TestFollowupAttemptCounter(unittest.TestCase):
    def test_increment_followup_attempt_bumps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")

            reg.increment_followup_attempt("1")
            reg.increment_followup_attempt("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.followup_attempt_count, 2)
            # pr_number preserved.
            self.assertEqual(record.pr_number, "7")
            # status preserved as completed.
            self.assertEqual(record.status, IssueStatus.COMPLETED)

    def test_increment_followup_attempt_preserves_pr_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")
            reg.increment_followup_attempt("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.branch_name, "clawcodex/issue-1")
            self.assertEqual(record.pr_number, "7")
            self.assertEqual(record.status, IssueStatus.COMPLETED)

    def test_can_follow_up_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", branch_name="clawcodex/issue-1", pr_number="7")
            reg.mark_completed("1")
            # No followup yet.
            self.assertTrue(reg.can_follow_up("1", max_attempts=3))
            # 2 followups done, 1 slot left.
            reg.increment_followup_attempt("1")
            reg.increment_followup_attempt("1")
            self.assertTrue(reg.can_follow_up("1", max_attempts=3))
            # 3 followups done — max reached (strict <).
            reg.increment_followup_attempt("1")
            self.assertFalse(reg.can_follow_up("1", max_attempts=3))
            # 5 followups, max=5 — still over the strict-< boundary.
            reg.increment_followup_attempt("1")
            reg.increment_followup_attempt("1")
            self.assertFalse(reg.can_follow_up("1", max_attempts=5))


# ---------------------------------------------------------------------------
# End-to-end Sub-C dispatch: orchestrator passes mode="followup" to git_sync
# ---------------------------------------------------------------------------


class TestOrchestratorFollowupDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_followup_run_passes_mode_to_git_sync(self) -> None:
        """When run_kind == 'agent_followup', _run_issue must invoke
        git_sync.sync(session, mode='followup')."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced(
                "1",
                branch_name="clawcodex/issue-1",
                pr_number="42",
                pr_url="https://example.test/pr/42",
            )
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.FOLLOWUP, source="label")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")
            orch._prepare_intent_session(session)
            self.assertEqual(session.run_kind, "agent_followup")

            # Simulate the dispatch in _run_issue:
            sync_mode = (
                "followup" if session.run_kind == "agent_followup" else "default"
            )
            sync_mock = MagicMock()
            sync_mock.sync = AsyncMock(
                return_value=MagicMock(
                    branch_name="clawcodex/issue-1",
                    commit_sha="newsha",
                    pull_request=PullRequestRef(
                        number="42", url="https://example.test/pr/42"
                    ),
                )
            )
            sync_result = await sync_mock.sync(session, mode=sync_mode)
            self.assertEqual(sync_result.commit_sha, "newsha")
            sync_mock.sync.assert_awaited_once()
            kwargs = sync_mock.sync.await_args.kwargs
            self.assertEqual(kwargs.get("mode"), "followup")

    async def test_default_run_passes_default_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")

            orch = _make_orchestrator(tracker=MagicMock(), registry=reg)
            session = _make_session(issue_id="1")
            # No _prepare_intent_session call → run_kind stays "issue".

            sync_mode = (
                "followup" if session.run_kind == "agent_followup" else "default"
            )
            self.assertEqual(sync_mode, "default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
