"""Tests for Analytics subsystem."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.analytics.events import (
    AnalyticsEvent,
    EventType,
    log_event,
    set_analytics_sink,
)
from src.services.analytics.metadata import (
    SessionAnalyticsMetadata,
    collect_session_metadata,
)
from src.services.analytics.sink import ConsoleSink, FileSink, NullSink


class TestEventTypes(unittest.TestCase):
    def test_all_event_types(self) -> None:
        self.assertEqual(EventType.SESSION_START.value, "session_start")
        self.assertEqual(EventType.TOOL_USE.value, "tool_use")
        self.assertEqual(EventType.COMPACT.value, "compact")

    def test_analytics_event(self) -> None:
        event = AnalyticsEvent(
            type=EventType.TOOL_USE,
            session_id="s1",
            model="claude-sonnet-4-6",
            data={"tool": "Bash", "command": "ls"},
        )
        self.assertEqual(event.type, EventType.TOOL_USE)
        self.assertGreater(event.timestamp, 0)


class TestAnalyticsSinks(unittest.TestCase):
    def test_null_sink(self) -> None:
        sink = NullSink()
        event = AnalyticsEvent(type=EventType.SESSION_START)
        sink.emit(event)  # Should not raise

    def test_file_sink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            sink = FileSink(path)

            for i in range(3):
                sink.emit(AnalyticsEvent(
                    type=EventType.TURN_START,
                    session_id="test",
                    data={"turn": i},
                ))
            sink.flush()

            lines = path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["type"], "turn_start")

    def test_file_sink_auto_flush(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            sink = FileSink(path)
            sink._max_buffer = 2

            sink.emit(AnalyticsEvent(type=EventType.TURN_START))
            # Buffer has 1 item, not yet flushed

            sink.emit(AnalyticsEvent(type=EventType.TURN_END))
            # Should have auto-flushed at buffer=2
            self.assertTrue(path.exists())
            lines = path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)

    def test_file_sink_close(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            sink = FileSink(path)
            sink.emit(AnalyticsEvent(type=EventType.SESSION_END))
            sink.close()
            self.assertTrue(path.exists())


class TestLogEvent(unittest.TestCase):
    def test_log_event_returns_event(self) -> None:
        set_analytics_sink(NullSink())
        event = log_event(EventType.SESSION_START, session_id="s1", model="m1")
        self.assertEqual(event.type, EventType.SESSION_START)
        self.assertEqual(event.session_id, "s1")

    def test_log_event_with_data(self) -> None:
        set_analytics_sink(NullSink())
        event = log_event(EventType.TOOL_USE, tool="Bash", command="ls")
        self.assertEqual(event.data["tool"], "Bash")


class TestSessionMetadata(unittest.TestCase):
    def test_collect_metadata(self) -> None:
        meta = collect_session_metadata(
            session_id="test-session",
            model="claude-sonnet-4-6",
            is_non_interactive=True,
        )
        self.assertEqual(meta.session_id, "test-session")
        self.assertEqual(meta.model, "claude-sonnet-4-6")
        self.assertTrue(meta.is_non_interactive)
        self.assertGreater(len(meta.os_name), 0)
        self.assertGreater(len(meta.python_version), 0)


if __name__ == "__main__":
    unittest.main()
