from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from extensions.api.query import QueryConfig, QueryRunner, SessionComplete, ToolCallEvent


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
