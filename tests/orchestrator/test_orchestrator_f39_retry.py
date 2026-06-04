"""F-39 Sub-B: agent:retry reset + remote PR close.

Covers:
  - TrackerAdapter.close_pull_request default returns False (no-op)
  - RepositoryIssueClient.close_pull_request PATCH /pulls state=closed
  - RepositoryIssueClient treats 422 (merged PR) as success
  - RepositoryTrackerAdapter delegates to client
  - LocalTrackerAdapter.close_pull_request is a no-op success
  - LinearAdapter.close_pull_request returns False (TODO)
  - IssueRegistry.reset_for_retry clears state + bumps retry_count
  - IssueRegistry.reset_for_retry on missing record is a no-op
  - Orchestrator._prepare_intent_reset closes remote PR + resets
  - Orchestrator._prepare_intent_reset is no-op for NONE / FOLLOWUP
  - close_pull_request on missing number is a no-op
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from extensions.orchestrator.issue_registry import (
    IssueRecord,
    IssueRegistry,
    IssueStatus,
)
from extensions.orchestrator.local_tracker.adapter import LocalTrackerAdapter
from extensions.orchestrator.orchestrator import Orchestrator, OrchestratorState
from extensions.orchestrator.agent_runner import AgentSession, RetryItem
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.repo_tracker.adapter import (
    RepositoryTrackerAdapter,
)
from extensions.orchestrator.repo_tracker.client import (
    RepositoryIssueClient,
    RepositoryTrackerError,
)
from extensions.orchestrator.tracker import (
    Intent,
    PullRequestRef,
    TrackerAdapter,
)
from extensions.orchestrator.config.schema import WorkflowConfig


# ---------------------------------------------------------------------------
# TrackerAdapter default
# ---------------------------------------------------------------------------


class _StubAdapter(TrackerAdapter):
    """Minimal concrete subclass for testing the default no-op."""

    async def fetch_candidate_issues(self) -> list:
        return []

    async def fetch_issue_states_by_ids(self, issue_ids):
        return {}

    async def create_comment(self, issue_id, body):
        return None

    async def update_issue_state(self, issue_id, state) -> None:
        return None


class TestTrackerAdapterCloseDefault(unittest.IsolatedAsyncioTestCase):
    async def test_default_close_pull_request_returns_false(self) -> None:
        adapter = _StubAdapter()
        result = await adapter.close_pull_request(
            PullRequestRef(number="1", url="https://example.test/pr/1")
        )
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# RepositoryIssueClient.close_pull_request
# ---------------------------------------------------------------------------


class TestRepositoryIssueClientClose(unittest.IsolatedAsyncioTestCase):
    async def test_close_issues_patch_request(self) -> None:
        client = RepositoryIssueClient(
            platform="github",
            owner="o",
            repo="r",
            api_key="dummy",
        )
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(return_value={"number": 7, "state": "closed"}),
        ) as mock:
            result = await client.close_pull_request(
                PullRequestRef(number="7", url="https://example.test/pr/7")
            )
        self.assertTrue(result)
        # Verify PATCH was called with the right endpoint and payload.
        mock.assert_called_once()
        args, kwargs = mock.call_args
        self.assertEqual(args[0], "PATCH")
        self.assertEqual(args[1], "/repos/o/r/pulls/7")
        payload = kwargs.get("json") or kwargs.get("data")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["state"], "closed")

    async def test_close_returns_false_when_number_missing(self) -> None:
        client = RepositoryIssueClient(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch.object(
            client, "_request_json", new=AsyncMock()
        ) as mock:
            result = await client.close_pull_request(
                PullRequestRef(number=None, url=None)
            )
        self.assertFalse(result)
        mock.assert_not_called()

    async def test_close_treats_422_as_success(self) -> None:
        client = RepositoryIssueClient(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(
                side_effect=RepositoryTrackerError(
                    "request_failed status=422 body=merged"
                )
            ),
        ):
            result = await client.close_pull_request(
                PullRequestRef(number="7", url="https://example.test/pr/7")
            )
        self.assertTrue(result)

    async def test_close_returns_false_on_other_4xx_5xx(self) -> None:
        client = RepositoryIssueClient(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(
                side_effect=RepositoryTrackerError(
                    "request_failed status=500 body=server error"
                )
            ),
        ):
            result = await client.close_pull_request(
                PullRequestRef(number="7", url="https://example.test/pr/7")
            )
        self.assertFalse(result)

    async def test_close_uses_form_data_for_access_token_platforms(self) -> None:
        client = RepositoryIssueClient(
            platform="gitcode", owner="o", repo="r", api_key="dummy"
        )
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(return_value={"number": 7, "state": "closed"}),
        ) as mock:
            result = await client.close_pull_request(
                PullRequestRef(number="7")
            )
        self.assertTrue(result)
        args, kwargs = mock.call_args
        # Gitee/GitCode use form-data not JSON.
        self.assertIsNone(kwargs.get("json"))
        self.assertEqual(kwargs.get("data", {}).get("state"), "closed")


# ---------------------------------------------------------------------------
# RepositoryTrackerAdapter close (delegate)
# ---------------------------------------------------------------------------


class TestRepositoryTrackerAdapterClose(unittest.IsolatedAsyncioTestCase):
    async def test_delegate_to_client(self) -> None:
        adapter = RepositoryTrackerAdapter(
            platform="github", owner="o", repo="r", api_key="dummy"
        )
        with patch.object(
            adapter.client,
            "close_pull_request",
            new=AsyncMock(return_value=True),
        ) as mock:
            result = await adapter.close_pull_request(
                PullRequestRef(number="7")
            )
        self.assertTrue(result)
        mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# LocalTrackerAdapter close
# ---------------------------------------------------------------------------


class TestLocalTrackerAdapterClose(unittest.IsolatedAsyncioTestCase):
    async def test_local_close_is_noop_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = LocalTrackerAdapter(issues_path=tmp)
            result = await adapter.close_pull_request(
                PullRequestRef(number="9")
            )
            self.assertTrue(result)


# ---------------------------------------------------------------------------
# IssueRegistry.reset_for_retry
# ---------------------------------------------------------------------------


class TestIssueRegistryResetForRetry(unittest.TestCase):
    def test_reset_clears_pr_commit_report_and_resets_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced(
                "1",
                branch_name="clawcodex/issue-1-foo",
                commit_sha="abc123",
                pr_number="42",
                pr_url="https://example.test/pr/42",
            )
            reg.update_report(
                "1",
                report_path="/tmp/report.md",
                verification_status="passed",
                verification_output="ok",
            )
            reg.mark_completed("1")

            reg.reset_for_retry("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.PENDING)
            self.assertIsNone(record.commit_sha)
            self.assertIsNone(record.pr_number)
            self.assertIsNone(record.pr_url)
            self.assertIsNone(record.report_path)
            self.assertIsNone(record.summary_comment_id)
            self.assertIsNone(record.verification_status)
            self.assertIsNone(record.verification_output)
            self.assertIsNone(record.last_hook_error)
            # retry_count bumped from 0 → 1.
            self.assertEqual(record.retry_count, 1)
            # branch_name is preserved (so a follow-up run knows the
            # git history target).
            self.assertEqual(record.branch_name, "clawcodex/issue-1-foo")
            # intent preserved for audit
            self.assertEqual(record.intent, Intent.NONE)

    def test_reset_increments_retry_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.reset_for_retry("1")
            reg.reset_for_retry("1")
            reg.reset_for_retry("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.retry_count, 3)

    def test_reset_with_increment_false_does_not_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.reset_for_retry("1", increment_retry=False)
            reg.reset_for_retry("1", increment_retry=False)
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.retry_count, 0)

    def test_reset_on_missing_record_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            self.assertIsNone(reg.reset_for_retry("missing"))

    def test_reset_preserves_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_intent("1", Intent.RETRY, source="label")
            reg.reset_for_retry("1")
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.intent, Intent.RETRY)
            self.assertEqual(record.intent_source, "label")

    def test_reset_json_round_trip(self) -> None:
        """After reset, registry.json on disk reflects the cleared state."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            reg = IssueRegistry(path)
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced(
                "1",
                branch_name="clawcodex/issue-1",
                commit_sha="deadbeef",
                pr_number="42",
            )
            reg.mark_completed("1")
            reg.reset_for_retry("1")

            payload = json.loads(path.read_text(encoding="utf-8"))
            entry = payload["1"]
            self.assertEqual(entry["status"], IssueStatus.PENDING.value)
            self.assertIsNone(entry["commit_sha"])
            self.assertIsNone(entry["pr_number"])
            self.assertIsNone(entry["pr_url"])
            self.assertIsNone(entry["report_path"])
            self.assertEqual(entry["retry_count"], 1)


# ---------------------------------------------------------------------------
# Orchestrator._prepare_intent_reset
# ---------------------------------------------------------------------------


def _make_workflow() -> WorkflowConfig:
    return WorkflowConfig()


def _make_orchestrator_for_test(
    *,
    tracker: Any,
    registry: IssueRegistry,
) -> Orchestrator:
    """Construct an Orchestrator bypassing the full __init__ to avoid
    needing a real WorkspaceManager / AgentRunner / status_dashboard.

    We only need `_prepare_intent_reset` and `_resolve_intent`, both of
    which read self.workflow, self.tracker, self._registry.
    """
    workflow = _make_workflow()
    orch = Orchestrator.__new__(Orchestrator)
    orch.workflow = workflow
    orch.tracker = tracker
    orch.workspace = MagicMock()
    orch.agent_runner = MagicMock()
    orch.status_dashboard = MagicMock()
    orch._registry = registry
    orch._state = MagicMock()
    return orch


class TestPrepareIntentReset(unittest.IsolatedAsyncioTestCase):
    async def test_retry_intent_closes_pr_and_resets_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            reg = IssueRegistry(path)
            reg.register(issue_id="77", issue_identifier="ISSUE-77")
            reg.mark_synced(
                "77",
                branch_name="clawcodex/issue-77",
                commit_sha="abc",
                pr_number="42",
                pr_url="https://example.test/pr/42",
            )
            reg.mark_completed("77")
            reg.mark_intent("77", Intent.RETRY, source="label")

            close_mock = AsyncMock(return_value=True)
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "77"
            await orch._prepare_intent_reset(issue)

            # PR close was attempted with the registry's PR.
            close_mock.assert_awaited_once()
            closed_pr = close_mock.await_args.args[0]
            self.assertEqual(closed_pr.number, "42")
            self.assertEqual(closed_pr.url, "https://example.test/pr/42")

            # Registry was reset.
            record = reg.get("77")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.PENDING)
            self.assertIsNone(record.commit_sha)
            self.assertIsNone(record.pr_number)
            self.assertEqual(record.retry_count, 1)
            self.assertEqual(record.branch_name, "clawcodex/issue-77")

    async def test_retry_continues_when_close_fails(self) -> None:
        """Even if the remote close fails, the local reset still happens."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", commit_sha="abc", pr_number="7")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.RETRY, source="label")

            close_mock = AsyncMock(return_value=False)
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "1"
            await orch._prepare_intent_reset(issue)

            close_mock.assert_awaited_once()
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.PENDING)
            self.assertEqual(record.retry_count, 1)

    async def test_retry_continues_when_close_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", commit_sha="abc", pr_number="7")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.RETRY, source="label")

            close_mock = AsyncMock(side_effect=RuntimeError("network down"))
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "1"
            await orch._prepare_intent_reset(issue)

            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.PENDING)

    async def test_no_intent_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", commit_sha="abc", pr_number="7")
            reg.mark_completed("1")
            # No mark_intent call.

            close_mock = AsyncMock()
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "1"
            await orch._prepare_intent_reset(issue)

            close_mock.assert_not_called()
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.COMPLETED)
            self.assertEqual(record.commit_sha, "abc")

    async def test_followup_intent_does_not_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_synced("1", commit_sha="abc", pr_number="7")
            reg.mark_completed("1")
            reg.mark_intent("1", Intent.FOLLOWUP, source="label")

            close_mock = AsyncMock()
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "1"
            await orch._prepare_intent_reset(issue)

            close_mock.assert_not_called()
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.COMPLETED)
            self.assertEqual(record.pr_number, "7")  # preserved for Sub-C

    async def test_no_record_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            close_mock = AsyncMock()
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "missing"
            await orch._prepare_intent_reset(issue)

            close_mock.assert_not_called()

    async def test_retry_with_no_pr_skips_close(self) -> None:
        """If there's no PR (e.g. agent never produced one), still reset."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            reg.register(issue_id="1", issue_identifier="ISSUE-1")
            reg.mark_failed("1")  # no PR, just a failure
            reg.mark_intent("1", Intent.RETRY, source="label")

            close_mock = AsyncMock()
            tracker = MagicMock()
            tracker.close_pull_request = close_mock

            orch = _make_orchestrator_for_test(tracker=tracker, registry=reg)
            issue = MagicMock()
            issue.id = "1"
            await orch._prepare_intent_reset(issue)

            close_mock.assert_not_called()
            record = reg.get("1")
            assert record is not None
            self.assertEqual(record.status, IssueStatus.PENDING)
            self.assertEqual(record.retry_count, 1)


# ---------------------------------------------------------------------------
# _schedule_retry: max_turns_exceeded uses a different delay base
# ---------------------------------------------------------------------------


def _make_orchestrator_for_schedule_retry_test(
    *,
    registry: IssueRegistry,
    workflow: WorkflowConfig,
) -> Orchestrator:
    """Bypass __init__ but keep a real _state so retry_queue /
    retry_attempts writes actually land in a real container."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.workflow = workflow
    orch.tracker = MagicMock()
    orch.workspace = MagicMock()
    orch.agent_runner = MagicMock()
    orch.status_dashboard = MagicMock()
    orch._registry = registry
    orch._state = OrchestratorState()
    return orch


def _make_session(status: str, issue_id: str = "77") -> AgentSession:
    return AgentSession(
        issue=Issue(id=issue_id, identifier=f"ISSUE-{issue_id}", title="retry test"),
        workspace=MagicMock(),
        status=status,
    )


class TestScheduleRetryMaxTurns(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_retry_default_uses_failure_base(self) -> None:
        """Without delay_base_ms, the default _FAILURE_RETRY_BASE_MS (10s)
        is used as the base for attempt=1."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            orch = _make_orchestrator_for_schedule_retry_test(
                registry=reg,
                workflow=WorkflowConfig(),
            )
            session = _make_session(status="failed")

            await orch._schedule_retry(session)

            self.assertEqual(len(orch._state.retry_queue), 1)
            retry = orch._state.retry_queue[0]
            self.assertEqual(retry.attempt, 1)
            self.assertEqual(retry.delay_seconds, 10.0)
            self.assertEqual(retry.error, "agent failed: failed")

    async def test_schedule_retry_uses_custom_base_for_max_turns(self) -> None:
        """When delay_base_ms=30_000 is passed, that value (not the 10s
        default) is used for attempt=1."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            orch = _make_orchestrator_for_schedule_retry_test(
                registry=reg,
                workflow=WorkflowConfig(),
            )
            session = _make_session(status="max_turns_exceeded")

            await orch._schedule_retry(session, delay_base_ms=30_000)

            retry = orch._state.retry_queue[0]
            self.assertEqual(retry.delay_seconds, 30.0)
            self.assertEqual(retry.error, "agent failed: max_turns_exceeded")
            self.assertEqual(
                orch._state.retry_attempts[session.issue.id], 1
            )

    async def test_schedule_retry_uses_max_turns_retry_delay_from_workflow(
        self,
    ) -> None:
        """Integration check: the orchestrator should be able to read
        workflow.agent.max_turns_retry_delay_ms and pass it through."""
        with tempfile.TemporaryDirectory() as tmp:
            reg = IssueRegistry(Path(tmp) / "registry.json")
            workflow = WorkflowConfig.from_dict(
                {"agent": {"max_turns_retry_delay_ms": 1234}}
            )
            orch = _make_orchestrator_for_schedule_retry_test(
                registry=reg,
                workflow=workflow,
            )
            session = _make_session(status="max_turns_exceeded")

            await orch._schedule_retry(
                session,
                delay_base_ms=workflow.agent.max_turns_retry_delay_ms,
            )

            retry = orch._state.retry_queue[0]
            self.assertEqual(retry.delay_seconds, 1.234)


if __name__ == "__main__":
    unittest.main(verbosity=2)
