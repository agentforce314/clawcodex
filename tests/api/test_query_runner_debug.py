from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from extensions.api.query import (
    QueryConfig,
    QueryRunner,
    SessionComplete,
    ToolCallEvent,
    ToolResultEvent,
)


class TestQueryRunnerDebug(unittest.IsolatedAsyncioTestCase):
    async def test_stream_writes_start_headless_event_and_heartbeat(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            debug_log = tmp_path / "debug.ndjson"

            def fake_run_headless_session(options) -> int:
                options.on_event(
                    SimpleNamespace(
                        kind="tool_use",
                        tool_name="Read",
                        tool_input={"file_path": "README.md"},
                        tool_output=None,
                        tool_use_id="tool-1",
                        is_error=False,
                        error=None,
                    )
                )
                time.sleep(0.08)
                options.stdout.write("final text")
                return 0

            clock = {"value": 0.0}

            def fake_monotonic() -> float:
                clock["value"] += 31.0
                return clock["value"]

            events = []
            runner = QueryRunner(
                QueryConfig(
                    prompt="hello",
                    workspace=tmp_path,
                    run_id="run-debug",
                    debug_log_path=debug_log,
                )
            )

            with patch(
                "extensions.capabilities.headless_runner.run_headless_session",
                fake_run_headless_session,
            ), patch("extensions.api.query.time.monotonic", fake_monotonic):
                async for event in runner.stream():
                    events.append(event)

            rows = [
                json.loads(line)
                for line in debug_log.read_text(encoding="utf-8").splitlines()
            ]
            stages = [row["stage"] for row in rows]

        self.assertTrue(any(isinstance(event, ToolCallEvent) for event in events))
        self.assertTrue(any(isinstance(event, SessionComplete) for event in events))
        self.assertIn("query_runner.start", stages)
        self.assertIn("headless.event", stages)
        self.assertIn("query_runner.heartbeat", stages)
        headless_event = next(row for row in rows if row["stage"] == "headless.event")
        self.assertEqual(headless_event["tool"], "Read")
        self.assertEqual(headless_event["tool_use_id"], "tool-1")

    async def test_stream_drains_queued_events_after_headless_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def fake_run_headless_session(options) -> int:
                options.on_event(
                    SimpleNamespace(
                        kind="tool_use",
                        tool_name="Bash",
                        tool_input={"command": "true"},
                        tool_output=None,
                        tool_use_id="tool-1",
                        is_error=False,
                        error=None,
                    )
                )
                options.on_event(
                    SimpleNamespace(
                        kind="tool_result",
                        tool_name="Bash",
                        tool_input=None,
                        tool_output="failed",
                        tool_use_id="tool-1",
                        is_error=True,
                        error="boom",
                    )
                )
                return 1

            runner = QueryRunner(
                QueryConfig(
                    prompt="hello",
                    workspace=tmp_path,
                    run_id="run-drain",
                    debug_log_path=tmp_path / "debug.ndjson",
                )
            )

            with patch(
                "extensions.capabilities.headless_runner.run_headless_session",
                fake_run_headless_session,
            ):
                events = [event async for event in runner.stream()]

        tool_events = [
            event for event in events if isinstance(event, (ToolCallEvent, ToolResultEvent))
        ]
        self.assertEqual([type(event) for event in tool_events], [ToolCallEvent, ToolResultEvent])
        result_event = tool_events[1]
        assert isinstance(result_event, ToolResultEvent)
        self.assertTrue(result_event.result["is_error"])
        self.assertEqual(result_event.result["error"], "boom")
        self.assertTrue(
            any(
                isinstance(event, SessionComplete) and event.reason == "exit_code=1"
                for event in events
            )
        )

    async def test_stream_backfills_tool_name_for_result_events(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            debug_log = tmp_path / "debug.ndjson"

            def fake_run_headless_session(options) -> int:
                options.on_event(
                    SimpleNamespace(
                        kind="tool_use",
                        tool_name="Glob",
                        tool_input={"pattern": "*.py"},
                        tool_output=None,
                        tool_use_id="tool-1",
                        is_error=False,
                        error=None,
                    )
                )
                options.on_event(
                    SimpleNamespace(
                        kind="tool_result",
                        tool_name="",
                        tool_input=None,
                        tool_output="missing path",
                        tool_use_id="tool-1",
                        is_error=True,
                        error="missing path",
                    )
                )
                return 1

            runner = QueryRunner(
                QueryConfig(
                    prompt="hello",
                    workspace=tmp_path,
                    run_id="run-tool-name",
                    debug_log_path=debug_log,
                )
            )

            with patch(
                "extensions.capabilities.headless_runner.run_headless_session",
                fake_run_headless_session,
            ):
                events = [event async for event in runner.stream()]

            rows = [
                json.loads(line)
                for line in debug_log.read_text(encoding="utf-8").splitlines()
            ]

        result_event = next(event for event in events if isinstance(event, ToolResultEvent))
        self.assertEqual(result_event.tool_name, "Glob")
        headless_results = [
            row
            for row in rows
            if row["stage"] == "headless.event" and row["kind"] == "tool_result"
        ]
        self.assertEqual(headless_results[0]["tool"], "Glob")
        self.assertEqual(headless_results[0]["error"], "missing path")
