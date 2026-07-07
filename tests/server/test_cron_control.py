"""Agent-server scheduled-task wiring: the worker's idle-branch firing
(_fire_due_scheduled), the wakeup fallback state machine, Esc/interrupt
clearing the pending wakeup, the skill_command control (/loop expansion for
the TUI), /clear dropping session tasks, and cron_status event dedupe."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.scheduled_tasks import SessionCronScheduler
from src.server.agent_server import AgentServerConfig, _AgentSession


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 7, 7, 12, 0, 30).timestamp()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _make_session() -> tuple[_AgentSession, list[dict], Clock]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="cron-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=False),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    sess.session = MagicMock()
    sess.session.conversation.messages = []
    sess.tool_context = SimpleNamespace(workspace_trusted=True)
    sess._save_session = lambda: None  # type: ignore[method-assign]
    clock = Clock()
    sess.cron_scheduler = SessionCronScheduler(now_fn=clock, jitter=False)
    return sess, emitted, clock


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(sess._handle_control_request({
        "type": "control_request",
        "request_id": "req-1",
        "request": {"subtype": subtype, **params},
    }))


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


def _cron_events(emitted: list[dict]) -> list[dict]:
    return [e for e in emitted if e.get("subtype") == "cron_status"]


class TestIdleFiring(unittest.TestCase):
    def test_due_cron_job_runs_as_internal_turn(self) -> None:
        sess, emitted, clock = _make_session()
        turns: list[tuple] = []
        sess._run_turn = lambda content, **kw: turns.append((content, kw)) or None  # type: ignore[method-assign]
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]

        job = sess.cron_scheduler.create("* * * * *", "check the deploy")
        self.assertFalse(sess._fire_due_scheduled())  # not due yet
        clock.advance(31)
        self.assertTrue(sess._fire_due_scheduled())

        (content, kwargs) = turns[0]
        self.assertIn("check the deploy", content)
        self.assertIn("<scheduled-task", content)
        self.assertIn(job.id, content)
        self.assertTrue(kwargs.get("internal"))
        # The recurring job advanced instead of disappearing.
        self.assertEqual(len(sess.cron_scheduler.list_jobs()), 1)
        # A fired line + snapshot rode the event stream.
        messages = [e.get("message") for e in _cron_events(emitted)]
        self.assertTrue(any("fired" in (m or "") for m in messages))

    def test_one_shot_envelope_notes_removal(self) -> None:
        sess, _, clock = _make_session()
        turns: list[str] = []
        sess._run_turn = lambda content, **kw: turns.append(content) or None  # type: ignore[method-assign]
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]
        sess.cron_scheduler.create("* * * * *", "once", recurring=False)
        clock.advance(31)
        sess._fire_due_scheduled()
        self.assertIn("one-shot task", turns[0])
        self.assertEqual(sess.cron_scheduler.list_jobs(), [])

    def test_init_error_blocks_firing(self) -> None:
        sess, _, clock = _make_session()
        sess.init_error = "refused"
        sess.cron_scheduler.create("* * * * *", "x")
        clock.advance(61)
        self.assertFalse(sess._fire_due_scheduled())


class TestWakeupFallback(unittest.TestCase):
    def _fire_one_wakeup(self, sess, clock, on_turn=None) -> None:
        sess._deliver_task_notifications = lambda: False
        sess._run_turn = lambda content, **kw: (on_turn() if on_turn else None)
        clock.advance(61)
        self.assertTrue(sess._fire_due_scheduled())

    def test_iteration_without_reschedule_arms_one_fallback(self) -> None:
        sess, emitted, clock = _make_session()
        sess.cron_scheduler.set_wakeup(60, "/loop check ci", "watching CI")
        self._fire_one_wakeup(sess, clock)  # turn does nothing

        info = sess.cron_scheduler.wakeup_info()
        self.assertIsNotNone(info)
        self.assertTrue(info.is_fallback)
        self.assertEqual(info.prompt, "/loop check ci")
        self.assertTrue(any("fallback" in (e.get("message") or "").lower()
                            for e in _cron_events(emitted)))

    def test_fallback_iteration_without_reschedule_ends_loop(self) -> None:
        sess, emitted, clock = _make_session()
        sess.cron_scheduler.set_wakeup(60, "/loop check ci", "watching CI")
        self._fire_one_wakeup(sess, clock)          # arms the fallback
        clock.advance(1200)
        self.assertTrue(sess._fire_due_scheduled())  # fallback fires, no reschedule

        self.assertIsNone(sess.cron_scheduler.wakeup_info())
        self.assertTrue(any("Loop ended" in (e.get("message") or "")
                            for e in _cron_events(emitted)))

    def test_reschedule_during_turn_skips_fallback(self) -> None:
        sess, _, clock = _make_session()
        sess.cron_scheduler.set_wakeup(60, "/loop check ci", "watching CI")
        self._fire_one_wakeup(
            sess, clock,
            on_turn=lambda: sess.cron_scheduler.set_wakeup(300, "/loop check ci", "still building"),
        )
        info = sess.cron_scheduler.wakeup_info()
        self.assertFalse(info.is_fallback)
        self.assertEqual(info.reason, "still building")

    def test_stop_during_turn_ends_loop(self) -> None:
        sess, emitted, clock = _make_session()
        sess.cron_scheduler.set_wakeup(60, "/loop check ci", "watching CI")
        self._fire_one_wakeup(
            sess, clock, on_turn=lambda: sess.cron_scheduler.clear_wakeup()
        )
        self.assertIsNone(sess.cron_scheduler.wakeup_info())
        self.assertTrue(any("Loop ended" in (e.get("message") or "")
                            for e in _cron_events(emitted)))

    def test_wakeup_envelope_carries_loop_instructions(self) -> None:
        sess, _, clock = _make_session()
        turns: list[str] = []
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]
        sess._run_turn = lambda content, **kw: turns.append(content) or None  # type: ignore[method-assign]
        sess.cron_scheduler.set_wakeup(60, "/loop check ci", "watching CI")
        clock.advance(61)
        sess._fire_due_scheduled()
        self.assertIn("<scheduled-wakeup", turns[0])
        self.assertIn("/loop check ci", turns[0])
        self.assertIn("ScheduleWakeup", turns[0])
        self.assertIn("Skill tool", turns[0])


class TestInterruptClearsWakeup(unittest.TestCase):
    def test_esc_clears_pending_wakeup_but_not_jobs(self) -> None:
        sess, emitted, clock = _make_session()
        sess.cron_scheduler.set_wakeup(300, "/loop check ci", "watching CI")
        job = sess.cron_scheduler.create("*/5 * * * *", "still here")

        _control(sess, "interrupt")

        self.assertIsNone(sess.cron_scheduler.wakeup_info())
        self.assertEqual([j.id for j in sess.cron_scheduler.list_jobs()], [job.id])
        self.assertTrue(any("Esc" in (e.get("message") or "")
                            for e in _cron_events(emitted)))

    def test_interrupt_without_wakeup_stays_silent(self) -> None:
        sess, emitted, _ = _make_session()
        _control(sess, "interrupt")
        self.assertEqual(_cron_events(emitted), [])


class TestSkillCommandControl(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_skills()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._reset_skills()
        self._tmp.cleanup()

    @staticmethod
    def _reset_skills() -> None:
        from src.skills.bundled import reset_bundled_skills_init_flag
        from src.skills.bundled_skills import clear_bundled_skills
        from src.skills.loader import (
            clear_dynamic_skills,
            clear_skill_caches,
            clear_skill_registry,
        )

        clear_bundled_skills()
        reset_bundled_skills_init_flag()
        clear_skill_caches()
        clear_dynamic_skills()
        clear_skill_registry()

    def test_expands_loop_fixed_mode(self) -> None:
        from src.tool_system.context import ToolContext

        sess, emitted, _ = _make_session()
        sess.tool_context = ToolContext(workspace_root=self.root)
        _control(sess, "skill_command", name="loop", args="5m check the deploy")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["name"], "loop")
        self.assertIn("fixed recurring interval", reply["prompt"])
        self.assertIn("check the deploy", reply["prompt"])
        self.assertIn("CronCreate", reply["prompt"])

    def test_expands_loop_dynamic_mode_with_schedule_wakeup(self) -> None:
        from src.tool_system.context import ToolContext

        sess, emitted, _ = _make_session()
        sess.tool_context = ToolContext(workspace_root=self.root)
        _control(sess, "skill_command", name="loop", args="check whether CI passed")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertIn("dynamic rescheduling", reply["prompt"])
        self.assertIn("ScheduleWakeup", reply["prompt"])
        self.assertIn("/loop check whether CI passed", reply["prompt"])

    def test_unknown_skill_reports_error(self) -> None:
        from src.tool_system.context import ToolContext

        sess, emitted, _ = _make_session()
        sess.tool_context = ToolContext(workspace_root=self.root)
        _control(sess, "skill_command", name="definitely-not-a-skill", args="")
        reply = _last_reply(emitted)
        self.assertFalse(reply["ok"])
        self.assertIn("definitely-not-a-skill", reply["error"])

    def test_missing_name_rejected(self) -> None:
        sess, emitted, _ = _make_session()
        _control(sess, "skill_command", name="", args="")
        self.assertFalse(_last_reply(emitted)["ok"])


class TestClearDropsScheduledTasks(unittest.TestCase):
    def test_clear_removes_jobs_and_wakeup(self) -> None:
        sess, emitted, _ = _make_session()
        sess.cron_scheduler.create("*/5 * * * *", "x")
        sess.cron_scheduler.set_wakeup(300, "/loop", "r")

        _control(sess, "clear")

        self.assertTrue(_last_reply(emitted)["ok"])
        self.assertEqual(sess.cron_scheduler.list_jobs(), [])
        self.assertIsNone(sess.cron_scheduler.wakeup_info())


class TestCronStatePush(unittest.TestCase):
    def test_messageless_push_dedupes_unchanged_snapshots(self) -> None:
        sess, emitted, _ = _make_session()
        sess.cron_scheduler.create("*/5 * * * *", "x")
        sess._push_cron_state()
        sess._push_cron_state()
        self.assertEqual(len(_cron_events(emitted)), 1)
        sess.cron_scheduler.set_wakeup(120, "/loop", "r")
        sess._push_cron_state()
        self.assertEqual(len(_cron_events(emitted)), 2)

    def test_snapshot_truncates_prompt_previews(self) -> None:
        sess, _, _ = _make_session()
        sess.cron_scheduler.create("*/5 * * * *", "p" * 500)
        payload = sess._cron_state_payload()
        self.assertLessEqual(len(payload["jobs"][0]["prompt_preview"]), 121)
        self.assertEqual(payload["jobs"][0]["human_schedule"], "every 5 minutes")


if __name__ == "__main__":
    unittest.main()
