"""R5 round-5 (ch17) — date-change (midnight-rollover) companion.

Port of TS getDateChangeAttachments: the memoized env date stays cache-stable,
and this appends a tail <system-reminder> with today's date on rollover so a
multi-day session isn't stuck showing the session-start date.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import src.context_system.date_change as dc
from src.bootstrap.state import get_last_emitted_date, set_last_emitted_date


class TestDateChangeReminder(unittest.TestCase):
    def setUp(self):
        set_last_emitted_date(None)

    def tearDown(self):
        set_last_emitted_date(None)

    def test_first_turn_records_no_reminder(self):
        with patch.object(dc, "_current_date_iso", return_value="2026-07-02"):
            r = dc.get_date_change_reminder()
        self.assertIsNone(r)
        self.assertEqual(get_last_emitted_date(), "2026-07-02")

    def test_same_day_no_reminder(self):
        set_last_emitted_date("2026-07-02")
        with patch.object(dc, "_current_date_iso", return_value="2026-07-02"):
            r = dc.get_date_change_reminder()
        self.assertIsNone(r)

    def test_rollover_emits_and_records(self):
        set_last_emitted_date("2026-07-02")
        with patch.object(dc, "_current_date_iso", return_value="2026-07-03"):
            r = dc.get_date_change_reminder()
        self.assertIsNotNone(r)
        self.assertIn("2026-07-03", r)
        self.assertIn("<system-reminder>", r)
        # critic MAJOR: the TS "DO NOT mention" behavioral guard must be present.
        self.assertIn("DO NOT mention this to the user", r)
        self.assertEqual(get_last_emitted_date(), "2026-07-03")

    def test_only_emits_once_per_rollover(self):
        set_last_emitted_date("2026-07-02")
        with patch.object(dc, "_current_date_iso", return_value="2026-07-03"):
            first = dc.get_date_change_reminder()
            second = dc.get_date_change_reminder()
        self.assertIsNotNone(first)
        self.assertIsNone(second)  # recorded → no re-emit same day

    def test_never_raises_on_state_error(self):
        with patch("src.bootstrap.state.get_last_emitted_date",
                   side_effect=RuntimeError("boom")):
            self.assertIsNone(dc.get_date_change_reminder())

    def test_current_date_is_live_date_only(self):
        # It must be a live date-only string (not the memoized session date,
        # not a per-second timestamp).
        val = dc._current_date_iso()
        self.assertRegex(val, r"^\d{4}-\d{2}-\d{2}$")


class TestLoopWiring(unittest.TestCase):
    def test_rollover_reminder_appended_on_real_turn(self):
        # Drives the agent-loop seam: a rollover appends a meta user message
        # to the query WORKING set (params.messages), not the persisted
        # conversation. We mock query() as an async generator that captures
        # what it received and stops the loop.
        import asyncio
        from unittest.mock import MagicMock
        from src.query import agent_loop_compat as alc

        appended: list = []

        async def _capture_query(params, **kwargs):
            appended.extend(params.messages)
            raise _StopLoop()
            yield  # noqa — unreachable; makes this an async generator

        async def _fake_recall(*a, **k):
            return None

        set_last_emitted_date("2026-07-02")
        with patch.object(alc, "_maybe_recall_memories", _fake_recall), \
                patch("src.context_system.date_change._current_date_iso",
                      return_value="2026-07-03"), \
                patch.object(alc, "query", _capture_query):
            try:
                asyncio.run(alc.run_query_as_agent_loop(
                    initial_messages=[{"role": "user", "content": "hi"}],
                    provider=MagicMock(),
                    tool_registry=MagicMock(list_tools=lambda: []),
                    tool_context=MagicMock(),
                    memory_recall_enabled=True,
                ))
            except _StopLoop:
                pass

        self.assertTrue(
            any("2026-07-03" in str(getattr(m, "content", m)) for m in appended),
            "date-change reminder should be in the query working set",
        )

    def test_no_reminder_on_internal_turn(self):
        # internal/notification turns (memory_recall_enabled=False) must NOT
        # get the date-change reminder.
        import asyncio
        from unittest.mock import MagicMock
        from src.query import agent_loop_compat as alc

        appended: list = []

        async def _capture_query(params, **kwargs):
            appended.extend(params.messages)
            raise _StopLoop()
            yield

        set_last_emitted_date("2026-07-02")
        with patch("src.context_system.date_change._current_date_iso",
                   return_value="2026-07-03"), \
                patch.object(alc, "query", _capture_query):
            try:
                asyncio.run(alc.run_query_as_agent_loop(
                    initial_messages=[{"role": "user", "content": "hi"}],
                    provider=MagicMock(),
                    tool_registry=MagicMock(list_tools=lambda: []),
                    tool_context=MagicMock(),
                    memory_recall_enabled=False,
                ))
            except _StopLoop:
                pass

        self.assertFalse(
            any("2026-07-03" in str(getattr(m, "content", m)) for m in appended))


class _StopLoop(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
