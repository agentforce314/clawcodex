"""CronCreate/CronList/CronDelete + ScheduleWakeup tool handlers: the
scheduler-backed path (real firing jobs), the legacy inert-dict fallback
for contexts without a scheduler (subagents/SDK), and input validation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.scheduled_tasks import SessionCronScheduler
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.tools import (
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    ScheduleWakeupTool,
)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 7, 7, 12, 0, 30).timestamp()

    def __call__(self) -> float:
        return self.t


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ctx = ToolContext(workspace_root=Path(self._tmp.name).resolve())
        self.clock = Clock()
        self.sched = SessionCronScheduler(now_fn=self.clock, jitter=False)
        self.ctx.cron_scheduler = self.sched

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestCronCreate(_Base):
    def test_registers_a_firing_job(self) -> None:
        out = CronCreateTool.call(
            {"cron": "*/5 * * * *", "prompt": "check the deploy"}, self.ctx
        ).output
        self.assertEqual(len(out["id"]), 8)
        self.assertEqual(out["humanSchedule"], "every 5 minutes")
        self.assertTrue(out["recurring"])
        self.assertGreater(out["nextFireAt"], self.clock())
        self.assertIsNotNone(out["expiresAt"])
        (job,) = self.sched.list_jobs()
        self.assertEqual(job.id, out["id"])

    def test_one_shot_has_no_expiry(self) -> None:
        out = CronCreateTool.call(
            {"cron": "7 13 * * *", "prompt": "once", "recurring": False}, self.ctx
        ).output
        self.assertFalse(out["recurring"])
        self.assertIsNone(out["expiresAt"])

    def test_invalid_cron_is_a_tool_input_error(self) -> None:
        with self.assertRaises(ToolInputError):
            CronCreateTool.call({"cron": "every day", "prompt": "x"}, self.ctx)

    def test_missing_fields_rejected(self) -> None:
        with self.assertRaises(ToolInputError):
            CronCreateTool.call({"cron": " ", "prompt": "x"}, self.ctx)
        with self.assertRaises(ToolInputError):
            CronCreateTool.call({"cron": "* * * * *", "prompt": ""}, self.ctx)

    def test_legacy_fallback_without_scheduler(self) -> None:
        self.ctx.cron_scheduler = None
        out = CronCreateTool.call(
            {"cron": "* * * * *", "prompt": "inert"}, self.ctx
        ).output
        self.assertIn(out["id"], self.ctx.crons)
        self.assertEqual(self.sched.list_jobs(), [])  # nothing scheduled


class TestCronListAndDelete(_Base):
    def test_list_includes_jobs_and_wakeup(self) -> None:
        created = CronCreateTool.call(
            {"cron": "*/5 * * * *", "prompt": "check"}, self.ctx
        ).output
        self.sched.set_wakeup(120, "/loop check", "waiting on CI")
        out = CronListTool.call({}, self.ctx).output
        self.assertEqual([j["id"] for j in out["jobs"]], [created["id"]])
        self.assertEqual(out["jobs"][0]["humanSchedule"], "every 5 minutes")
        self.assertEqual(out["pendingWakeup"]["reason"], "waiting on CI")

    def test_delete_by_id(self) -> None:
        created = CronCreateTool.call(
            {"cron": "* * * * *", "prompt": "x"}, self.ctx
        ).output
        out = CronDeleteTool.call({"id": created["id"]}, self.ctx).output
        self.assertTrue(out["success"])
        self.assertEqual(self.sched.list_jobs(), [])
        again = CronDeleteTool.call({"id": created["id"]}, self.ctx).output
        self.assertFalse(again["success"])


class TestScheduleWakeup(_Base):
    def test_schedules_with_clamped_delay(self) -> None:
        out = ScheduleWakeupTool.call(
            {"delaySeconds": 5, "prompt": "/loop check ci", "reason": "watching CI"},
            self.ctx,
        ).output
        self.assertTrue(out["scheduled"])
        self.assertEqual(out["delaySeconds"], 60)  # clamped up to the minimum
        info = self.sched.wakeup_info()
        self.assertEqual(info.prompt, "/loop check ci")
        self.assertEqual(info.reason, "watching CI")

    def test_stop_clears_the_pending_wakeup(self) -> None:
        self.sched.set_wakeup(300, "/loop", "r")
        out = ScheduleWakeupTool.call({"stop": True}, self.ctx).output
        self.assertTrue(out["stopped"])
        self.assertTrue(out["clearedPendingWakeup"])
        self.assertIsNone(self.sched.wakeup_info())
        again = ScheduleWakeupTool.call({"stop": True}, self.ctx).output
        self.assertFalse(again["clearedPendingWakeup"])

    def test_missing_fields_rejected_unless_stop(self) -> None:
        with self.assertRaises(ToolInputError):
            ScheduleWakeupTool.call({"prompt": "/loop", "reason": "r"}, self.ctx)
        with self.assertRaises(ToolInputError):
            ScheduleWakeupTool.call({"delaySeconds": 60, "reason": "r"}, self.ctx)
        with self.assertRaises(ToolInputError):
            ScheduleWakeupTool.call({"delaySeconds": 60, "prompt": "/loop"}, self.ctx)

    def test_unavailable_without_scheduler(self) -> None:
        self.ctx.cron_scheduler = None
        result = ScheduleWakeupTool.call(
            {"delaySeconds": 60, "prompt": "/loop", "reason": "r"}, self.ctx
        )
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
