"""SessionCronScheduler: job lifecycle (cap, one-shot delete, recurring
advance, no catch-up, 7-day final-fire expiry), the deterministic jitter
rules, the single wakeup slot (clamp, replace, stop, action tracking), the
disable env flag, and snapshot/restore resume rules."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from src.scheduled_tasks import (
    MAX_JOBS,
    RECURRING_EXPIRY_SECONDS,
    SessionCronScheduler,
)


class Clock:
    """Controllable time source pinned to a real wall-clock minute."""

    def __init__(self, start: datetime) -> None:
        self.t = start.timestamp()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make(jitter: bool = False, start: datetime | None = None) -> tuple[SessionCronScheduler, Clock]:
    clock = Clock(start or datetime(2026, 7, 7, 12, 0, 30))
    return SessionCronScheduler(now_fn=clock, jitter=jitter), clock


class TestJobs(unittest.TestCase):
    def test_create_validates_cron(self) -> None:
        sched, _ = make()
        with self.assertRaises(ValueError):
            sched.create("nope", "x")

    def test_ids_are_eight_chars(self) -> None:
        sched, _ = make()
        job = sched.create("* * * * *", "x")
        self.assertEqual(len(job.id), 8)

    def test_job_cap_enforced(self) -> None:
        sched, _ = make()
        for _ in range(MAX_JOBS):
            sched.create("* * * * *", "x")
        with self.assertRaises(ValueError):
            sched.create("* * * * *", "one too many")

    def test_delete(self) -> None:
        sched, _ = make()
        job = sched.create("* * * * *", "x")
        self.assertTrue(sched.delete(job.id))
        self.assertFalse(sched.delete(job.id))
        self.assertEqual(sched.list_jobs(), [])

    def test_not_due_pops_nothing(self) -> None:
        sched, _ = make()
        sched.create("* * * * *", "x")
        self.assertEqual(sched.pop_due(), [])

    def test_recurring_fires_and_advances(self) -> None:
        sched, clock = make()
        job = sched.create("* * * * *", "check the deploy")
        clock.advance(31)  # cross the 12:01 boundary
        fired = sched.pop_due()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].kind, "cron")
        self.assertEqual(fired[0].prompt, "check the deploy")
        self.assertFalse(fired[0].deleted)
        (advanced,) = sched.list_jobs()
        self.assertEqual(advanced.id, job.id)
        self.assertGreater(advanced.next_fire_at, clock())
        self.assertEqual(advanced.fired_count, 1)

    def test_no_catch_up_for_missed_fires(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "x")
        clock.advance(600)  # ten missed minutes
        self.assertEqual(len(sched.pop_due()), 1)  # fires ONCE
        self.assertEqual(sched.pop_due(), [])

    def test_one_shot_deletes_after_fire(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "once", recurring=False)
        clock.advance(31)
        fired = sched.pop_due()
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0].deleted)
        self.assertFalse(fired[0].recurring)
        self.assertEqual(sched.list_jobs(), [])

    def test_recurring_expiry_final_fire_then_delete(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "x")
        clock.advance(RECURRING_EXPIRY_SECONDS + 61)
        fired = sched.pop_due()
        self.assertEqual(len(fired), 1)
        self.assertTrue(fired[0].deleted)
        self.assertTrue(fired[0].recurring)
        self.assertEqual(sched.list_jobs(), [])
        self.assertEqual(sched.pop_due(), [])


class TestJitter(unittest.TestCase):
    def test_recurring_offset_bounded_by_half_interval(self) -> None:
        sched, clock = make(jitter=True)
        job = sched.create("* * * * *", "x")  # 60s interval → offset ∈ [0, 30]
        base = datetime(2026, 7, 7, 12, 1).timestamp()
        self.assertGreaterEqual(job.next_fire_at, base)
        self.assertLessEqual(job.next_fire_at - base, 30.0)

    def test_recurring_offset_capped_at_thirty_minutes(self) -> None:
        sched, _ = make(jitter=True)
        job = sched.create("0 9 * * *", "x")  # daily → cap 1800s
        base = datetime(2026, 7, 8, 9, 0).timestamp()
        self.assertGreaterEqual(job.next_fire_at, base)
        self.assertLessEqual(job.next_fire_at - base, 1800.0)

    def test_offset_is_deterministic_per_id(self) -> None:
        sched, clock = make(jitter=True)
        job = sched.create("* * * * *", "x")
        first_offset = job.next_fire_at - datetime(2026, 7, 7, 12, 1).timestamp()
        clock.advance(job.next_fire_at - clock() + 1)
        sched.pop_due()
        (advanced,) = sched.list_jobs()
        # Advance lands on some later minute boundary; offset must repeat.
        base = datetime.fromtimestamp(advanced.next_fire_at - first_offset)
        self.assertEqual(base.second, 0)
        self.assertEqual(base.microsecond, 0)

    def test_one_shot_on_the_hour_fires_early(self) -> None:
        sched, _ = make(jitter=True)
        job = sched.create("0 13 * * *", "x", recurring=False)
        pinned = datetime(2026, 7, 7, 13, 0).timestamp()
        self.assertLessEqual(job.next_fire_at, pinned)
        self.assertLessEqual(pinned - job.next_fire_at, 90.0)

    def test_one_shot_off_hour_has_no_jitter(self) -> None:
        sched, _ = make(jitter=True)
        job = sched.create("7 13 * * *", "x", recurring=False)
        self.assertEqual(job.next_fire_at, datetime(2026, 7, 7, 13, 7).timestamp())


class TestWakeup(unittest.TestCase):
    def test_delay_clamped_to_bounds(self) -> None:
        sched, clock = make()
        low = sched.set_wakeup(5, "/loop", "too soon")
        self.assertAlmostEqual(low.fire_at - clock(), 60.0)
        high = sched.set_wakeup(999_999, "/loop", "too late")
        self.assertAlmostEqual(high.fire_at - clock(), 3600.0)

    def test_single_slot_replaces(self) -> None:
        sched, _ = make()
        sched.set_wakeup(60, "/loop a", "first")
        sched.set_wakeup(120, "/loop b", "second")
        info = sched.wakeup_info()
        self.assertEqual(info.prompt, "/loop b")

    def test_clear_reports_whether_pending(self) -> None:
        sched, _ = make()
        self.assertFalse(sched.clear_wakeup())
        sched.set_wakeup(60, "/loop", "r")
        self.assertTrue(sched.clear_wakeup())
        self.assertIsNone(sched.wakeup_info())

    def test_pop_due_fires_wakeup_once(self) -> None:
        sched, clock = make()
        sched.set_wakeup(60, "/loop check ci", "watching CI")
        self.assertEqual(sched.pop_due(), [])
        clock.advance(61)
        fired = sched.pop_due()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].kind, "wakeup")
        self.assertEqual(fired[0].prompt, "/loop check ci")
        self.assertEqual(fired[0].reason, "watching CI")
        self.assertIsNone(sched.wakeup_info())
        self.assertEqual(sched.pop_due(), [])

    def test_action_tracking_for_fallback_decision(self) -> None:
        sched, _ = make()
        sched.begin_turn_window()
        self.assertIsNone(sched.wakeup_action_since())
        sched.set_wakeup(60, "/loop", "r")
        self.assertEqual(sched.wakeup_action_since(), "set")
        sched.begin_turn_window()
        sched.clear_wakeup()
        self.assertEqual(sched.wakeup_action_since(), "stopped")
        # A fallback arm is the SERVER's doing, not the model's — it must
        # not read as "the iteration rescheduled".
        sched.begin_turn_window()
        sched.set_wakeup(1200, "/loop", "fallback", is_fallback=True)
        self.assertIsNone(sched.wakeup_action_since())


class TestDisableFlag(unittest.TestCase):
    def test_disable_env_blocks_everything(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "x")
        clock.advance(61)
        with patch.dict("os.environ", {"CLAWCODEX_DISABLE_CRON": "1"}):
            self.assertEqual(sched.pop_due(), [])
            with self.assertRaises(ValueError):
                sched.create("* * * * *", "y")
            with self.assertRaises(ValueError):
                sched.set_wakeup(60, "/loop", "r")
        # Flag removed → the scheduler resumes.
        self.assertEqual(len(sched.pop_due()), 1)

    def test_claude_code_spelling_honored(self) -> None:
        sched, _ = make()
        with patch.dict("os.environ", {"CLAUDE_CODE_DISABLE_CRON": "true"}):
            with self.assertRaises(ValueError):
                sched.create("* * * * *", "x")


class TestSnapshotRestore(unittest.TestCase):
    def test_roundtrip(self) -> None:
        sched, clock = make()
        job = sched.create("*/5 * * * *", "check")
        sched.set_wakeup(120, "/loop check", "waiting")
        snap = sched.snapshot()

        fresh = SessionCronScheduler(now_fn=clock, jitter=False)
        self.assertEqual(fresh.restore(snap), 2)
        (restored,) = fresh.list_jobs()
        self.assertEqual(restored.id, job.id)
        self.assertEqual(restored.cron, "*/5 * * * *")
        self.assertEqual(fresh.wakeup_info().prompt, "/loop check")

    def test_restore_applies_resume_rules(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "recurring stays")
        sched.create("59 23 31 12 *", "one-shot future", recurring=False)
        one_shot_past = sched.create("* * * * *", "one-shot past", recurring=False)
        sched.set_wakeup(60, "/loop", "past wakeup")
        snap = sched.snapshot()

        clock.advance(120)  # past the one-shot fire + wakeup, inside 7 days
        fresh = SessionCronScheduler(now_fn=clock, jitter=False)
        restored = fresh.restore(snap)
        prompts = {j.prompt for j in fresh.list_jobs()}
        self.assertIn("recurring stays", prompts)
        self.assertIn("one-shot future", prompts)
        self.assertNotIn("one-shot past", prompts)
        self.assertIsNone(fresh.wakeup_info())
        self.assertEqual(restored, 2)
        self.assertNotIn(one_shot_past.id, {j.id for j in fresh.list_jobs()})

    def test_restore_drops_expired_recurring(self) -> None:
        sched, clock = make()
        sched.create("* * * * *", "old recurring")
        snap = sched.snapshot()
        clock.advance(RECURRING_EXPIRY_SECONDS + 1)
        fresh = SessionCronScheduler(now_fn=clock, jitter=False)
        self.assertEqual(fresh.restore(snap), 0)
        self.assertEqual(fresh.list_jobs(), [])

    def test_restore_tolerates_garbage(self) -> None:
        sched, _ = make()
        self.assertEqual(sched.restore({"jobs": [{"id": "x"}, 42], "wakeup": {"fire_at": "soon"}}), 0)
        self.assertEqual(sched.restore("not a dict"), 0)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
