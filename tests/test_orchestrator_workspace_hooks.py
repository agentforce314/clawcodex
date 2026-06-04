from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.orchestrator.agent_runner import AgentSession
from src.orchestrator.config.schema import (
    AgentConfig,
    CodexConfig,
    PollingConfig,
    TrackerConfig,
    WorkflowConfig,
    WorkspaceConfig,
)
from src.orchestrator.git_sync import (
    GitSyncPostCommitError,
    GitSyncResult,
    VerificationFailed,
)
from src.orchestrator.issue import Issue
from extensions.orchestrator.issue_registry import IssueStatus
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.tracker import TrackerAdapter
from src.orchestrator.workspace import Workspace, WorkspaceHookError, WorkspaceManager


class _Tracker(TrackerAdapter):
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


class _HookWorkspaceManager(WorkspaceManager):
    def __init__(self) -> None:
        super().__init__(WorkspaceConfig(root=Path(tempfile.mkdtemp())))
        self.events: list[str] = []
        self.fail_before = False
        self.fail_after = False

    async def run_before_run_hook(
        self, workspace: Workspace, issue: Issue
    ) -> None:
        self.events.append("before_run")
        if self.fail_before:
            raise WorkspaceHookError("before failed")

    async def run_after_run_hook(
        self, workspace: Workspace, issue: Issue
    ) -> None:
        self.events.append("after_run")
        if self.fail_after:
            raise WorkspaceHookError("after failed")

    async def cleanup(self, issue: Issue) -> None:
        self.events.append("cleanup")


class _Runner:
    def __init__(self, events: list[str], *, should_fail: bool = False) -> None:
        self.max_turns = 2
        self._events = events
        self._should_fail = should_fail

    async def run(self, session: AgentSession, workflow: WorkflowConfig, **kwargs) -> None:
        self._events.append("agent_run")
        if self._should_fail:
            raise RuntimeError("agent failed")
        session.status = "completed"


class _PostCommitFailingSync:
    async def sync(self, session: AgentSession, *, mode: str = "default") -> None:
        result = GitSyncResult(
            branch_name="integration/f46",
            base_branch="main",
            commit_sha="abc123",
            committed=True,
            pending_review=True,
        )
        raise GitSyncPostCommitError(
            VerificationFailed("test verification failed", "pytest failed"),
            result,
        )


class TestOrchestratorWorkspaceHooks(unittest.IsolatedAsyncioTestCase):
    def _workflow(self) -> WorkflowConfig:
        return WorkflowConfig(
            tracker=TrackerConfig(
                kind="linear",
                api_key="mock-key",
                project_slug="proj",
            ),
            polling=PollingConfig(interval_ms=1000),
            agent=AgentConfig(
                max_concurrent_agents=1,
                max_turns=2,
                provider="anthropic",
                permission_mode="dontAsk",
            ),
            codex=CodexConfig(approval_policy="never"),
        )

    def _session(self) -> AgentSession:
        return AgentSession(
            issue=Issue(id="1", identifier="ISSUE-1", title="Test"),
            workspace=Workspace(
                path=Path(tempfile.mkdtemp()),
                issue_identifier="ISSUE-1",
                issue_id="1",
            ),
        )

    async def test_hooks_wrap_agent_run(self) -> None:
        workspace = _HookWorkspaceManager()
        runner = _Runner(workspace.events)
        orchestrator = Orchestrator(
            workflow=self._workflow(),
            tracker=_Tracker(),
            workspace=workspace,
            agent_runner=runner,
        )
        session = self._session()
        orchestrator._state.running[session.issue.id or "1"] = session

        await orchestrator._run_issue(session)

        self.assertEqual(
            workspace.events,
            ["before_run", "agent_run", "after_run", "cleanup"],
        )
        self.assertEqual(session.status, "completed")

    async def test_before_run_failure_skips_agent_and_after_run(self) -> None:
        workspace = _HookWorkspaceManager()
        workspace.fail_before = True
        runner = _Runner(workspace.events)
        orchestrator = Orchestrator(
            workflow=self._workflow(),
            tracker=_Tracker(),
            workspace=workspace,
            agent_runner=runner,
        )
        session = self._session()
        orchestrator._state.running[session.issue.id or "1"] = session

        await orchestrator._run_issue(session)

        self.assertEqual(
            workspace.events,
            ["before_run", "cleanup"],
        )
        self.assertEqual(session.status, "before_run_failed")

    async def test_after_run_still_runs_when_agent_fails(self) -> None:
        workspace = _HookWorkspaceManager()
        runner = _Runner(workspace.events, should_fail=True)
        orchestrator = Orchestrator(
            workflow=self._workflow(),
            tracker=_Tracker(),
            workspace=workspace,
            agent_runner=runner,
        )
        session = self._session()
        orchestrator._state.running[session.issue.id or "1"] = session

        await orchestrator._run_issue(session)

        self.assertEqual(
            workspace.events,
            ["before_run", "agent_run", "after_run", "cleanup"],
        )
        self.assertEqual(session.status, "failed")

    async def test_post_commit_sync_failure_records_commit_before_failure(self) -> None:
        workspace = _HookWorkspaceManager()
        runner = _Runner(workspace.events)
        orchestrator = Orchestrator(
            workflow=self._workflow(),
            tracker=_Tracker(),
            workspace=workspace,
            agent_runner=runner,
        )
        orchestrator.git_sync = _PostCommitFailingSync()
        session = self._session()
        orchestrator._registry.register(
            issue_id=session.issue.id or "1",
            issue_identifier=session.issue.identifier or "ISSUE-1",
            branch_name="integration/f46",
            base_branch="main",
        )
        orchestrator._state.running[session.issue.id or "1"] = session

        await orchestrator._run_issue(session)

        record = orchestrator._registry.get(session.issue.id or "1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.commit_sha, "abc123")
        self.assertEqual(record.branch_name, "integration/f46")
        self.assertEqual(record.status, IssueStatus.VERIFICATION_FAILED)
        self.assertEqual(record.verification_output, "pytest failed")
