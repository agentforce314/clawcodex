from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.api.query import SessionComplete
from src.orchestrator.agent_runner import AgentRunner, AgentSession
from src.orchestrator.config.schema import AgentConfig, CodexConfig, WorkflowConfig
from src.orchestrator.issue import Issue
from src.orchestrator.workspace import Workspace


class _QueryRunnerStub:
    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield SessionComplete(reason="success")


class _Comment:
    def __init__(self, id: str) -> None:
        self.id = id


class _CommentTracker:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    async def create_comment(self, issue_id: str, body: str) -> _Comment:
        self.comments.append((issue_id, body))
        return _Comment("summary-1")


class _ProgressReporter:
    def __init__(self) -> None:
        self.events: list[object] = []

    def on_event(self, event, session) -> None:
        self.events.append(event)


class TestAgentRunnerF38(unittest.IsolatedAsyncioTestCase):
    async def test_run_posts_summary_placeholder_and_writes_phase_event_log(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-77",
                issue_id="77",
            )
            session = AgentSession(
                issue=Issue(id="77", identifier="ISSUE-77", title="Run reports"),
                workspace=workspace,
            )
            tracker = _CommentTracker()
            progress = _ProgressReporter()
            runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())

            with patch("src.orchestrator.agent_runner.QueryRunner", _QueryRunnerStub):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    comment_tracker=tracker,
                    progress_reporter=progress,
                )

            event_log = workspace.path / ".event_logs" / "77.ndjson"
            contents = event_log.read_text(encoding="utf-8")

        self.assertEqual(session.status, "completed")
        self.assertRegex(session.run_id or "", r"^run-01-\d{8}T\d{6}Z$")
        self.assertEqual(session.summary_comment_id, "summary-1")
        self.assertEqual(
            tracker.comments,
            [("77", "## ClawCodex Run Summary\n\n⏳ Run in progress.")],
        )
        self.assertEqual(session.turn_count, 1)
        self.assertEqual(len(progress.events), 1)
        self.assertIn('"type": "phase_complete"', contents)
        self.assertIn('"phase": 1', contents)

    def test_followup_run_id_uses_issue_and_followup_attempts(self) -> None:
        with TemporaryDirectory() as tmp:
            session = AgentSession(
                issue=Issue(id="77"),
                workspace=Workspace(
                    path=Path(tmp),
                    issue_identifier="ISSUE-77",
                    issue_id="77",
                ),
                run_kind="review_followup",
                attempt=4,
                issue_attempt=3,
                followup_attempt=2,
            )
            runner = AgentRunner(AgentConfig(), CodexConfig())

            run_id = runner._build_run_id(session)

        self.assertRegex(run_id, r"^run-3-followup-2-\d{8}T\d{6}Z$")
