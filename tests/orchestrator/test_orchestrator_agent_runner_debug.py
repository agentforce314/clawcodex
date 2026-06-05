from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.api.query import SessionComplete, TextDelta, ToolCallEvent
from extensions.orchestrator.agent_runner import AgentRunner, AgentSession
from extensions.orchestrator.config.schema import AgentConfig, CodexConfig, WorkflowConfig
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.workspace import Workspace


class _ToolCallThenSuccessStub:
    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield ToolCallEvent(tool_name="Read", params={"file_path": "README.md"})
        yield TextDelta(content="done")
        yield SessionComplete(reason="success")


class TestOrchestratorAgentRunnerDebug(unittest.IsolatedAsyncioTestCase):
    async def test_run_writes_debug_log_and_session_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-78",
                issue_id="78",
            )
            session = AgentSession(
                issue=Issue(id="78", identifier="ISSUE-78", title="Debug run"),
                workspace=workspace,
            )
            runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                _ToolCallThenSuccessStub,
            ):
                await runner.run(session, WorkflowConfig.from_dict({}))

            assert session.debug_log_path is not None
            debug_log = Path(session.debug_log_path)
            rows = [
                json.loads(line)
                for line in debug_log.read_text(encoding="utf-8").splitlines()
            ]
            stages = [row["stage"] for row in rows]

        self.assertEqual(session.status, "completed")
        self.assertEqual(session.turn_count, 1)
        self.assertEqual(session.tool_count, 1)
        self.assertEqual(session.last_agent_event, "SessionComplete")
        self.assertEqual(session.last_tool_name, "Read")
        self.assertIn("agent_runner.start", stages)
        self.assertIn("agent_runner.turn_start", stages)
        self.assertIn("agent_runner.event", stages)
        self.assertIn("agent_runner.turn_complete", stages)
        self.assertTrue(str(debug_log).endswith("debug.ndjson"))

    async def test_run_invokes_diagnostics_callback_for_stream_events(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-79",
                issue_id="79",
            )
            session = AgentSession(
                issue=Issue(id="79", identifier="ISSUE-79", title="Debug updates"),
                workspace=workspace,
            )
            runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())
            snapshots: list[tuple[str | None, int, int, int]] = []

            def diagnostics_callback(active_session: AgentSession) -> None:
                snapshots.append(
                    (
                        active_session.last_agent_event,
                        active_session.turn_count,
                        active_session.tool_count,
                        len(active_session.output_text),
                    )
                )

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                _ToolCallThenSuccessStub,
            ):
                await runner.run(
                    session,
                    WorkflowConfig.from_dict({}),
                    diagnostics_callback=diagnostics_callback,
                )

        self.assertEqual(snapshots[0][0], None)
        self.assertEqual(snapshots[0][1:], (0, 0, 0))
        self.assertEqual(
            [event for event, _, _, _ in snapshots[1:]],
            ["ToolCallEvent", "TextDelta", "SessionComplete"],
        )
        self.assertEqual(snapshots[1][2], 1)
        self.assertEqual(snapshots[2][2], 1)
        self.assertEqual(snapshots[3][1], 1)
        self.assertGreater(snapshots[3][3], 0)

    async def test_run_does_not_wait_on_resume_event_unless_paused(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-80",
                issue_id="80",
            )
            session = AgentSession(
                issue=Issue(id="80", identifier="ISSUE-80", title="Not paused"),
                workspace=workspace,
                pause_resume_event=asyncio.Event(),
            )
            runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                _ToolCallThenSuccessStub,
            ):
                await asyncio.wait_for(
                    runner.run(session, WorkflowConfig.from_dict({})),
                    timeout=1,
                )

        self.assertEqual(session.status, "completed")
        self.assertEqual(session.tool_count, 1)
