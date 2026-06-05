from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from extensions.orchestrator.agent_runner import AgentSession
from extensions.orchestrator.config.schema import WorkflowConfig
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.issue_registry import IssueRegistry, IssueStatus
from extensions.orchestrator.orchestrator import Orchestrator
from extensions.orchestrator.tracker import TrackerAdapter
from extensions.orchestrator.workspace import Workspace, WorkspaceConfig, WorkspaceManager


class _Tracker(TrackerAdapter):
    active_states = ["open"]

    async def fetch_candidate_issues(self) -> list[Issue]:
        return []

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> dict[str, Issue]:
        return {}

    async def create_comment(self, issue_id: str, body: str) -> Any:
        return None

    async def update_issue_state(self, issue_id: str, state: str) -> None:
        return None


class _HangingRunner:
    max_turns = 1

    async def run(self, session: AgentSession, workflow: WorkflowConfig, **kwargs: Any) -> None:
        session.run_id = "run-timeout"
        session.debug_log_path = str(session.workspace.path / ".orchestrator_control" / "runs" / session.run_id / "debug.ndjson")
        session.turn_count = 2
        session.tool_count = 3
        session.last_agent_event = "ToolCallEvent"
        session.last_tool_name = "Read"
        session.output_text = "partial output"
        await asyncio.Event().wait()


class _WorkspaceManager(WorkspaceManager):
    def __init__(self, root: Path) -> None:
        super().__init__(WorkspaceConfig(root=root))
        self.cleaned: list[str | None] = []
        self.terminal_cleanup_ran = False

    async def run_terminal_workspace_cleanup(self) -> None:
        self.terminal_cleanup_ran = True

    async def run_before_run_hook(self, workspace: Workspace, issue: Issue) -> None:
        return None

    async def run_after_run_hook(self, workspace: Workspace, issue: Issue) -> None:
        return None

    async def cleanup(self, issue: Issue) -> None:
        self.cleaned.append(issue.id)


class TestOrchestratorAgentWatchdog(unittest.IsolatedAsyncioTestCase):
    def test_agent_run_timeout_config_default_and_override(self) -> None:
        self.assertEqual(WorkflowConfig.from_dict({}).agent.run_timeout_ms, 1_800_000)
        config = WorkflowConfig.from_dict({"agent": {"run_timeout_ms": 1234}})
        self.assertEqual(config.agent.run_timeout_ms, 1234)

    def test_registry_lists_running_and_marks_failed_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = IssueRegistry(Path(tmp) / "registry.json")
            registry.register("1", "ISSUE-1", status=IssueStatus.RUNNING)
            registry.register("2", "ISSUE-2", status=IssueStatus.COMPLETED)

            self.assertEqual([record.issue_id for record in registry.running_records()], ["1"])

            registry.mark_failed_with_reason("1", "timed out")
            reloaded = IssueRegistry(Path(tmp) / "registry.json")
            record = reloaded.get("1")

        assert record is not None
        self.assertEqual(record.status, IssueStatus.FAILED)
        self.assertEqual(record.attempt_count, 1)
        self.assertEqual(record.verification_status, "failed")
        self.assertEqual(record.verification_output, "timed out")
        self.assertEqual(record.last_hook_error, "timed out")

    async def test_run_issue_times_out_agent_and_schedules_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowConfig.from_dict(
                {
                    "tracker": {"kind": "local", "issues_path": str(root / "issues")},
                    "workspace": {"root": str(root / "workspace")},
                    "agent": {"run_timeout_ms": 1, "max_retry_attempts": 2},
                }
            )
            workspace = _WorkspaceManager(root / "workspace")
            orchestrator = Orchestrator(
                workflow=workflow,
                tracker=_Tracker(),
                workspace=workspace,
                agent_runner=_HangingRunner(),  # type: ignore[arg-type]
            )
            issue = Issue(id="1", identifier="ISSUE-1", title="Timeout", state="open")
            session = AgentSession(
                issue=issue,
                workspace=Workspace(root / "workspace", "ISSUE-1", "1"),
                pause_resume_event=asyncio.Event(),
            )
            orchestrator._state.running["1"] = session
            orchestrator._registry.register("1", "ISSUE-1", status=IssueStatus.RUNNING)

            await orchestrator._run_issue(session)
            record = orchestrator._registry.get("1")
            assert session.debug_log_path is not None
            rows = [
                json.loads(line)
                for line in Path(session.debug_log_path).read_text(encoding="utf-8").splitlines()
            ]

        assert record is not None
        self.assertEqual(session.status, "agent_timeout")
        self.assertEqual(record.status, IssueStatus.FAILED)
        self.assertEqual(record.attempt_count, 1)
        self.assertIn("Agent run exceeded configured timeout", record.verification_output or "")
        self.assertEqual(record.run_id, "run-timeout")
        self.assertEqual(record.debug_log_path, session.debug_log_path)
        self.assertEqual(record.run_turn_count, 2)
        self.assertEqual(record.run_tool_count, 3)
        self.assertEqual(record.run_last_event, "ToolCallEvent")
        self.assertEqual(record.run_last_tool, "Read")
        self.assertEqual(record.run_output_len, len("partial output"))
        self.assertIsNotNone(record.run_timeout_deadline_at)
        timeout_event = next(row for row in rows if row["stage"] == "orchestrator.timeout")
        self.assertEqual(record.run_workspace_dirty, timeout_event["workspace_dirty"])
        self.assertEqual(timeout_event["run_id"], "run-timeout")
        self.assertEqual(timeout_event["turn_count"], 2)
        self.assertEqual(timeout_event["tool_count"], 3)
        self.assertEqual(timeout_event["last_event_type"], "ToolCallEvent")
        self.assertEqual(timeout_event["last_tool"], "Read")
        self.assertEqual(timeout_event["output_len"], len("partial output"))
        self.assertEqual(len(orchestrator._state.retry_queue), 1)
        self.assertEqual(orchestrator._state.retry_queue[0].issue_id, "1")
        self.assertEqual(workspace.cleaned, ["1"])
        self.assertNotIn("1", orchestrator._state.running)

    async def test_startup_recovers_stale_running_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowConfig.from_dict(
                {
                    "tracker": {"kind": "local", "issues_path": str(root / "issues")},
                    "workspace": {"root": str(root / "workspace")},
                }
            )
            workspace = _WorkspaceManager(root / "workspace")
            registry = IssueRegistry(root / "workspace" / ".clawcodex_issue_registry.json")
            registry.register("1", "ISSUE-1", status=IssueStatus.RUNNING)
            orchestrator = Orchestrator(
                workflow=workflow,
                tracker=_Tracker(),
                workspace=workspace,
                agent_runner=_HangingRunner(),  # type: ignore[arg-type]
            )

            await orchestrator._recover_stale_running_records()
            record = orchestrator._registry.get("1")

        assert record is not None
        self.assertEqual(record.status, IssueStatus.FAILED)
        self.assertEqual(record.attempt_count, 1)
        self.assertEqual(
            record.verification_output,
            "Recovered stale running issue on orchestrator startup",
        )
