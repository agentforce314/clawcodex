from __future__ import annotations

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
