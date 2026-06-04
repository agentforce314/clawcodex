"""F-45: Tool-call audit bypass tests.

Covers the per-tool NDJSON bypass and the report_writer field
that registers the audit path on the run report.  Mirrors the
pattern of tests/test_orchestrator_trackers.py (TestReportWriter):
TemporaryDirectory + HOME override + ReportResult / NDJSON assertions.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.api.query import (
    PhaseComplete,
    SessionComplete,
    ToolCallEvent,
    ToolResultEvent,
)
from extensions.orchestrator.agent_runner import AgentRunner, AgentSession
from extensions.orchestrator.config.schema import (
    AgentConfig,
    CodexConfig,
    WorkflowConfig,
)
from extensions.orchestrator.issue import Issue
from extensions.orchestrator.report_writer import (
    ReportResult,
    RunReport,
    write as report_writer_write,
)
from extensions.orchestrator.tool_event_log import ToolEventLog
from extensions.orchestrator.workspace import Workspace


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _tc_event(
    tool_name: str = "Bash",
    params: dict | None = None,
    *,
    approved: bool | None = None,
    deny_reason: str | None = None,
) -> ToolCallEvent:
    """Build a ToolCallEvent mirroring extensions.api.query.ToolCallEvent."""
    return ToolCallEvent(
        tool_name=tool_name,
        params=params or {"command": "ls -la"},
        tool_use_id="tool-use-1",
        _approved=approved,
        _deny_reason=deny_reason,
    )


def _run_with_event(tool_event: ToolCallEvent) -> None:
    """One-shot helper: drive AgentRunner._append_tool_event_log with an
    explicit session_context and HOME override.
    """
    runner = AgentRunner(AgentConfig(), CodexConfig())
    session_context = {
        "run_id": "run-99-20260602T000000Z",
        "permission_mode": "bypassPermissions",
        "turn": 3,
    }
    runner._append_tool_event_log(tool_event, session_context)


# ---------------------------------------------------------------------------
# Sub-B: ToolEventLog dataclass
# ---------------------------------------------------------------------------


class TestToolEventLogDataclass(unittest.TestCase):
    def test_to_dict_contains_all_eight_fields(self) -> None:
        row = ToolEventLog(
            tool="Bash",
            params={"command": "ls"},
            approved=True,
            deny_reason=None,
            permission_mode="bypassPermissions",
            turn=5,
            session_run_id="run-1",
            ts=1717350000.123,
        )
        d = row.to_dict()
        self.assertEqual(
            set(d.keys()),
            {
                "ts",
                "tool",
                "params",
                "approved",
                "deny_reason",
                "permission_mode",
                "turn",
                "session_run_id",
            },
        )
        self.assertEqual(d["tool"], "Bash")
        self.assertEqual(d["params"], {"command": "ls"})
        self.assertTrue(d["approved"])
        self.assertIsNone(d["deny_reason"])
        self.assertEqual(d["permission_mode"], "bypassPermissions")
        self.assertEqual(d["turn"], 5)
        self.assertEqual(d["session_run_id"], "run-1")
        self.assertEqual(d["ts"], 1717350000.123)

    def test_to_json_is_single_line(self) -> None:
        row = ToolEventLog(
            tool="Read",
            params={"file_path": "/etc/hosts"},
            approved=True,
            deny_reason=None,
            permission_mode="default",
            turn=1,
            session_run_id="r1",
        )
        line = row.to_json()
        self.assertNotIn("\n", line)
        parsed = json.loads(line)
        self.assertEqual(parsed["tool"], "Read")


# ---------------------------------------------------------------------------
# Sub-A: _append_tool_event_log writes valid NDJSON
# ---------------------------------------------------------------------------


class TestAppendToolEventLog(unittest.TestCase):
    def setUp(self) -> None:
        self._home = TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._home.cleanup()

    def test_writes_ndjson_under_tool_events_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # Bind HOME and the tool-events base to a controlled dir so
            # the test never pollutes the real user home.
            base = workspace / "home"
            base.mkdir()
            os.environ["HOME"] = str(base)

            # Run with a controlled run_id so we can locate the file.
            runner = AgentRunner(AgentConfig(), CodexConfig())
            runner._append_tool_event_log(
                _tc_event(approved=True),
                {
                    "run_id": "run-test-1",
                    "permission_mode": "bypassPermissions",
                    "turn": 0,
                },
            )
            log_path = (
                base / ".clawcodex" / "tool-events" / "run-test-1" / "events.ndjson"
            )
            self.assertTrue(log_path.exists(), f"missing: {log_path}")
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["tool"], "Bash")
            self.assertTrue(row["approved"])
            self.assertIsNone(row["deny_reason"])
            self.assertEqual(row["permission_mode"], "bypassPermissions")
            self.assertEqual(row["turn"], 0)
            self.assertEqual(row["session_run_id"], "run-test-1")
            self.assertIn("ts", row)

    def test_writes_multiple_rows_in_order(self) -> None:
        runner = AgentRunner(AgentConfig(), CodexConfig())
        ctx = {
            "run_id": "run-multi",
            "permission_mode": "dontAsk",
            "turn": 2,
        }
        for name, params in [
            ("Bash", {"command": "ls"}),
            ("Read", {"file_path": "/etc/hostname"}),
            ("Edit", {"file_path": "x.py", "old_string": "a", "new_string": "b"}),
        ]:
            runner._append_tool_event_log(
                _tc_event(tool_name=name, params=params, approved=True),
                ctx,
            )
        log_path = (
            Path(os.environ["HOME"])
            / ".clawcodex"
            / "tool-events"
            / "run-multi"
            / "events.ndjson"
        )
        rows = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").strip().splitlines()
        ]
        self.assertEqual([r["tool"] for r in rows], ["Bash", "Read", "Edit"])
        self.assertEqual(rows[0]["params"], {"command": "ls"})

    def test_deny_decision_records_reason(self) -> None:
        runner = AgentRunner(AgentConfig(), CodexConfig())
        runner._append_tool_event_log(
            _tc_event(approved=False, deny_reason="not in safe-list"),
            {
                "run_id": "run-deny",
                "permission_mode": "default",
                "turn": 4,
            },
        )
        log_path = (
            Path(os.environ["HOME"])
            / ".clawcodex"
            / "tool-events"
            / "run-deny"
            / "events.ndjson"
        )
        row = json.loads(
            log_path.read_text(encoding="utf-8").strip().splitlines()[0]
        )
        self.assertFalse(row["approved"])
        self.assertEqual(row["deny_reason"], "not in safe-list")

    def test_falls_back_to_unknown_when_run_id_missing(self) -> None:
        runner = AgentRunner(AgentConfig(), CodexConfig())
        runner._append_tool_event_log(
            _tc_event(approved=True),
            {
                # run_id intentionally missing
                "permission_mode": "acceptEdits",
                "turn": 1,
            },
        )
        log_path = (
            Path(os.environ["HOME"])
            / ".clawcodex"
            / "tool-events"
            / "unknown"
            / "events.ndjson"
        )
        self.assertTrue(log_path.exists())

    def test_appends_multiple_lines(self) -> None:
        runner = AgentRunner(AgentConfig(), CodexConfig())
        ctx = {"run_id": "run-x", "permission_mode": "default", "turn": 0}
        for _ in range(5):
            runner._append_tool_event_log(_tc_event(approved=True), ctx)
        log_path = (
            Path(os.environ["HOME"])
            / ".clawcodex"
            / "tool-events"
            / "run-x"
            / "events.ndjson"
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 5)

    def test_write_failures_are_swallowed(self) -> None:
        """Defensive: even if the file write itself raises, the agent
        run must not be affected.  We force a failure by pointing
        HOME at an unwritable path."""
        runner = AgentRunner(AgentConfig(), CodexConfig())
        bad_home = "/this-path-definitely-does-not-exist/clawcodex"
        os.environ["HOME"] = bad_home
        try:
            # Should NOT raise even though mkdir will fail.
            runner._append_tool_event_log(
                _tc_event(approved=True),
                {"run_id": "r1", "permission_mode": "default", "turn": 0},
            )
        except Exception as exc:  # pragma: no cover - this is the assertion
            self.fail(f"_append_tool_event_log raised: {exc}")


# ---------------------------------------------------------------------------
# Sub-A + Sub-D: end-to-end run emits NDJSON rows via QueryRunner stub
# ---------------------------------------------------------------------------


class _QueryRunnerWithToolCallStub:
    """Stub that yields one ToolCallEvent + SessionComplete."""

    def __init__(self, config) -> None:
        self.config = config

    async def stream(self):
        yield ToolCallEvent(
            tool_name="Bash",
            params={"command": "echo hi"},
            tool_use_id="t1",
        )
        yield ToolResultEvent(
            tool_name="Bash",
            result={"is_error": False},
        )
        yield SessionComplete(reason="success")


class TestAgentRunnerWiresAuditBypass(unittest.TestCase):
    """Verifies the run-loop wiring: _handle_tool_call is called,
    session.tool_events_path is set, NDJSON row is written."""

    def setUp(self) -> None:
        self._home = TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._home.cleanup()

    def test_run_writes_ndjson_row_and_sets_session_path(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(
                path=Path(tmp),
                issue_identifier="ISSUE-1",
                issue_id="1",
            )
            session = AgentSession(
                issue=Issue(id="1", identifier="ISSUE-1", title="audit"),
                workspace=workspace,
            )
            runner = AgentRunner(AgentConfig(max_turns=1), CodexConfig())

            with patch(
                "extensions.orchestrator.agent_runner.QueryRunner",
                _QueryRunnerWithToolCallStub,
            ):
                import asyncio
                asyncio.run(
                    runner.run(session, WorkflowConfig.from_dict({}))
                )

            self.assertIsNotNone(session.tool_events_path)
            self.assertTrue(session.tool_events_path.endswith("events.ndjson"))
            self.assertIn(session.run_id, session.tool_events_path)

            # Find the file under the (overridden) HOME.
            tool_events_path = Path(session.tool_events_path)
            self.assertTrue(
                tool_events_path.exists(),
                f"expected NDJSON at {tool_events_path}",
            )
            rows = [
                json.loads(line)
                for line in tool_events_path.read_text(
                    encoding="utf-8"
                ).strip().splitlines()
            ]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["tool"], "Bash")
            self.assertEqual(row["params"], {"command": "echo hi"})
            self.assertEqual(row["permission_mode"], "dontAsk")
            self.assertEqual(row["session_run_id"], session.run_id)
            # _handle_tool_call now runs in the headless loop, so the
            # row should have a real approved verdict, not None.
            self.assertIsNotNone(row["approved"])


# ---------------------------------------------------------------------------
# Sub-C: report_writer dual-writes NDJSON and renders markdown line
# ---------------------------------------------------------------------------


class TestReportWriterToolEventsPath(unittest.TestCase):
    def setUp(self) -> None:
        self._home = TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._home.cleanup()

    def test_run_report_field_defaults_to_none(self) -> None:
        # Backward compat: existing call sites that construct
        # RunReport without tool_events_path should still work.
        report = RunReport(
            run_id="r",
            tracker="github",
            owner=None,
            repo=None,
            issue_id="1",
            issue_identifier=None,
            issue_title=None,
            status="completed",
            branch_name=None,
            base_branch=None,
            commit_sha=None,
            pr_number=None,
            pr_url=None,
            turn_count=1,
            tool_count=2,
            verification_status=None,
            verification_output=None,
            output_excerpt="",
        )
        self.assertIsNone(report.tool_events_path)

    def test_write_dual_writes_ndjson_to_persistent_layer(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            # Pre-create a fake tool-events NDJSON to simulate the
            # agent_runner having written it.
            tool_events = (
                Path(os.environ["HOME"])
                / ".clawcodex"
                / "tool-events"
                / "run-x"
                / "events.ndjson"
            )
            tool_events.parent.mkdir(parents=True, exist_ok=True)
            tool_events.write_text(
                json.dumps(
                    {
                        "ts": 1.0,
                        "tool": "Bash",
                        "params": {"command": "ls"},
                        "approved": True,
                        "deny_reason": None,
                        "permission_mode": "bypassPermissions",
                        "turn": 0,
                        "session_run_id": "run-x",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = report_writer_write(
                run_id="run-x",
                workspace_path=workspace,
                tracker="github",
                owner="acme",
                repo="widget",
                issue=Issue(id="1", identifier="I-1", title="t"),
                status="completed",
                branch_name="b",
                base_branch="main",
                turn_count=1,
                tool_count=1,
                output_text="",
                tool_events_path=str(tool_events),
            )

            self.assertIsInstance(result, ReportResult)
            persistent_events = (
                Path(result.persistent_markdown_path).parent
                / "run-x.events.ndjson"
            )
            self.assertTrue(persistent_events.exists(), persistent_events)
            self.assertEqual(
                persistent_events.read_text(encoding="utf-8"),
                tool_events.read_text(encoding="utf-8"),
            )

    def test_markdown_includes_tool_events_line(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            tool_events = (
                Path(os.environ["HOME"])
                / ".clawcodex"
                / "tool-events"
                / "run-m"
                / "events.ndjson"
            )
            tool_events.parent.mkdir(parents=True, exist_ok=True)
            tool_events.write_text("{}\n", encoding="utf-8")

            result = report_writer_write(
                run_id="run-m",
                workspace_path=workspace,
                tracker="github",
                owner="o",
                repo="r",
                issue=Issue(id="2", identifier="I-2", title="t"),
                status="completed",
                output_text="",
                tool_events_path=str(tool_events),
            )
            md = Path(result.workspace_markdown_path).read_text(
                encoding="utf-8"
            )
            self.assertIn("Tool events:", md)
            self.assertIn(str(tool_events), md)

    def test_markdown_omits_tool_events_line_when_not_provided(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            result = report_writer_write(
                run_id="run-n",
                workspace_path=workspace,
                tracker="github",
                owner="o",
                repo="r",
                issue=Issue(id="3", identifier="I-3", title="t"),
                status="completed",
                output_text="",
            )
            md = Path(result.workspace_markdown_path).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Tool events:", md)


# ---------------------------------------------------------------------------
# Sub-E: 50MB rotation
# ---------------------------------------------------------------------------


class TestToolEventLogRotation(unittest.TestCase):
    def setUp(self) -> None:
        self._home = TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._home.cleanup()

    def test_rotate_when_threshold_exceeded(self) -> None:
        from extensions.orchestrator.agent_runner import (
            _TOOL_EVENT_LOG_ROTATE_BYTES,
        )
        runner = AgentRunner(AgentConfig(), CodexConfig())
        ctx = {
            "run_id": "run-rotate",
            "permission_mode": "default",
            "turn": 0,
        }
        # Pre-create a saturated events.ndjson at the rotation threshold.
        log_dir = (
            Path(os.environ["HOME"])
            / ".clawcodex"
            / "tool-events"
            / "run-rotate"
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "events.ndjson"
        log_path.write_bytes(b"x" * _TOOL_EVENT_LOG_ROTATE_BYTES)

        runner._append_tool_event_log(_tc_event(approved=True), ctx)

        # The old file should have been rotated to events.ndjson.1 and
        # the new file should now contain one valid row.
        rotated = log_dir / "events.ndjson.1"
        self.assertTrue(rotated.exists())
        self.assertEqual(
            rotated.stat().st_size, _TOOL_EVENT_LOG_ROTATE_BYTES
        )
        rows = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").strip().splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "Bash")


# ---------------------------------------------------------------------------
# Sub-F regression: 4 permission modes all emit NDJSON rows
# ---------------------------------------------------------------------------


class TestFourPermissionModes(unittest.TestCase):
    """Regression: all 4 headless-relevant permission_modes produce
    NDJSON rows with the same schema, only the column value varies."""

    def setUp(self) -> None:
        self._home = TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._home.cleanup()

    def test_all_four_modes_emit_rows(self) -> None:
        runner = AgentRunner(AgentConfig(), CodexConfig())
        for mode in (
            "bypassPermissions",
            "dontAsk",
            "acceptEdits",
            "default",
        ):
            ctx = {
                "run_id": f"run-{mode}",
                "permission_mode": mode,
                "turn": 0,
            }
            runner._append_tool_event_log(_tc_event(approved=True), ctx)
            log_path = (
                Path(os.environ["HOME"])
                / ".clawcodex"
                / "tool-events"
                / f"run-{mode}"
                / "events.ndjson"
            )
            self.assertTrue(
                log_path.exists(),
                f"missing log for mode={mode}",
            )
            row = json.loads(
                log_path.read_text(encoding="utf-8").strip().splitlines()[0]
            )
            self.assertEqual(row["permission_mode"], mode)
            # 8-field invariant across modes.
            self.assertEqual(
                set(row.keys()),
                {
                    "ts",
                    "tool",
                    "params",
                    "approved",
                    "deny_reason",
                    "permission_mode",
                    "turn",
                    "session_run_id",
                },
            )


# ---------------------------------------------------------------------------
# Sub-D: config defaults ignore workspace runtime artifacts
# ---------------------------------------------------------------------------


class TestWorkspaceConfigDefaults(unittest.TestCase):
    def test_runtime_artifacts_added_to_gitignore_patterns(self) -> None:
        wf = WorkflowConfig.from_dict({})
        self.assertIn(".event_logs", wf.workspace.gitignore_patterns)
        self.assertIn(".operator_hints.md", wf.workspace.gitignore_patterns)
        self.assertIn(".reports", wf.workspace.gitignore_patterns)
        self.assertNotIn("event_logs", wf.workspace.gitignore_patterns)


if __name__ == "__main__":
    unittest.main()
