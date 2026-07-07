"""Agent-server /goal wiring: the ``goal``/``subgoal`` controls, the worker's
post-turn continuation hook (double-checked locking, preemption, stale-verdict
discard, timeout parking), interrupt auto-pause, /clear integration, and
save/resume persistence. Critic-required coverage from the design review:
race, judge-timeout, interrupt, and continuation-decoration tests."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.goals import GoalJudgeTimeout
from src.server.agent_server import (
    AgentServerConfig,
    _AgentSession,
    _SHUTDOWN,
)


def _judge_returning(payload: str):
    return lambda system, user: payload


def _make_session(trusted: bool = True) -> tuple[_AgentSession, list[dict]]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="goal-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=False),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    sess.session = MagicMock()
    sess.session.conversation.messages = []
    sess.tool_context = SimpleNamespace(workspace_trusted=trusted)
    sess._save_session = lambda: None  # type: ignore[method-assign]
    return sess, emitted


def _set_goal(sess: _AgentSession, text: str = "make it green", judge=None) -> None:
    """Activate a goal directly through the manager (bypasses the gate)."""
    mgr = sess._goal_manager()
    mgr.set(text)
    if judge is not None:
        mgr.judge = judge
    # Pin the judge: _goal_manager() rebinds from the (mock) provider on
    # every call, which would clobber a test's scripted judge.
    sess._goal_manager = lambda: mgr  # type: ignore[method-assign]


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


def _goal_events(emitted: list[dict]) -> list[dict]:
    return [e for e in emitted if e.get("subtype") == "goal_status"]


# ── goal control ───────────────────────────────────────────────────────


class TestGoalControl(unittest.TestCase):
    def test_set_replies_with_kickoff_and_notice(self) -> None:
        sess, emitted = _make_session()
        with patch.object(_AgentSession, "_goal_set_gate", return_value=None):
            _control(sess, "goal", arg="ship the feature")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["kickoff"], "ship the feature")
        self.assertIn("Goal set", reply["notice"])
        self.assertTrue(reply["active"])
        self.assertTrue(sess._goal_mgr.is_active())

    def test_bare_goal_is_status(self) -> None:
        sess, emitted = _make_session()
        _control(sess, "goal", arg="")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertIn("No active goal", reply["text"])
        self.assertNotIn("kickoff", reply)

    def test_clear_aliases_clear(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "goal", arg="cancel")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertIn("cleared", reply["text"].lower())
        self.assertFalse(sess._goal_mgr.has_goal())

    def test_untrusted_workspace_refuses_set_with_reason(self) -> None:
        sess, emitted = _make_session(trusted=False)
        _control(sess, "goal", arg="do the thing")
        reply = _last_reply(emitted)
        self.assertFalse(reply["ok"])
        self.assertIn("trusted workspace", reply["error"])
        self.assertIsNone(sess._goal_mgr.state)
        # status and clear still answer under a closed gate
        _control(sess, "goal", arg="")
        self.assertTrue(_last_reply(emitted)["ok"])

    def test_hooks_disabled_refuses_set_with_reason(self) -> None:
        sess, emitted = _make_session(trusted=True)
        fake_settings = SimpleNamespace(hooks=SimpleNamespace(enabled=False))
        with patch("src.settings.settings.load_settings", return_value=fake_settings):
            _control(sess, "goal", arg="do the thing")
        reply = _last_reply(emitted)
        self.assertFalse(reply["ok"])
        self.assertIn("hooks are disabled", reply["error"])

    def test_subgoal_control(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "subgoal", arg="also update docs")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertIn("Added subgoal 1", reply["text"])
        self.assertEqual(sess._goal_mgr.state.subgoals, ["also update docs"])

    def test_clear_control_clears_goal(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        sess.session.conversation.clear = MagicMock()
        with patch("src.server.agent_server._cost_snapshot", return_value={}):
            _control(sess, "clear")
        reply = _last_reply(emitted)
        self.assertTrue(reply["ok"])
        self.assertFalse(sess._goal_mgr.has_goal())

    def test_clear_control_strips_goal_from_disk(self) -> None:
        """/clear + immediate quit must not leave an active goal for
        --resume to restore — the handler strips the on-disk goal key
        (the normal save path can't: the conversation is now empty)."""
        import src.server.agent_server as srv

        with __import__("tempfile").TemporaryDirectory() as td:
            tmp = __import__("pathlib").Path(td)
            f = tmp / "goal-sess.json"
            f.write_text(json.dumps({
                "session_id": "goal-sess",
                "conversation": {"messages": [{"role": "user", "content": "x"}]},
                "goal": {"goal": "old goal", "status": "active"},
            }))
            with patch.object(srv, "_sessions_dir", return_value=tmp):
                sess, emitted = _make_session()
                _set_goal(sess, "old goal")
                sess.session.conversation.clear = MagicMock()
                with patch("src.server.agent_server._cost_snapshot", return_value={}):
                    _control(sess, "clear")
            self.assertTrue(_last_reply(emitted)["ok"])
            self.assertNotIn("goal", json.loads(f.read_text()))


# ── interrupt auto-pause ───────────────────────────────────────────────


class TestInterruptPausesGoal(unittest.TestCase):
    def test_interrupt_pauses_active_goal_and_emits_status(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "interrupt")
        self.assertEqual(sess._goal_mgr.state.status, "paused")
        events = _goal_events(emitted)
        self.assertTrue(events and "paused" in events[-1]["message"].lower())
        self.assertFalse(events[-1]["goal_active"])

    def test_interrupt_without_goal_is_silent(self) -> None:
        sess, emitted = _make_session()
        _control(sess, "interrupt")
        self.assertEqual(_goal_events(emitted), [])


# ── post-turn continuation hook ────────────────────────────────────────


class TestMaybeContinueGoal(unittest.TestCase):
    def test_continue_enqueues_marked_continuation(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "continue", "reason": "tests still red"}'
        ))
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        item = sess._inbox.get_nowait()
        self.assertTrue(item.get("__goal__"))
        self.assertIn("tests still red", item["content"])
        events = _goal_events(emitted)
        self.assertTrue(events and events[-1]["message"].startswith("↻"))
        self.assertTrue(events[-1]["goal_active"])

    def test_done_marks_goal_and_does_not_enqueue(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "done", "reason": "all pass"}'
        ))
        sess._maybe_continue_goal({"subtype": "success", "response_text": "10 passed"})
        self.assertTrue(sess._inbox.empty())
        self.assertEqual(sess._goal_mgr.state.status, "done")
        events = _goal_events(emitted)
        self.assertTrue(events and events[-1]["message"].startswith("✓"))
        self.assertFalse(events[-1]["goal_active"])

    def test_queued_user_input_preempts(self) -> None:
        sess, _ = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "continue", "reason": "wip"}'
        ))
        sess._inbox.put("a real user prompt")
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        # Only the user prompt is queued — no continuation stacked behind it,
        # and no judge turn was even counted (preflight bailed).
        self.assertEqual(sess._inbox.get_nowait(), "a real user prompt")
        self.assertTrue(sess._inbox.empty())
        self.assertEqual(sess._goal_mgr.state.turns_used, 0)

    def test_cancelled_and_error_turns_skip_judging(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "continue", "reason": "wip"}'
        ))
        sess._maybe_continue_goal({"subtype": "cancelled", "response_text": ""})
        sess._maybe_continue_goal({"subtype": "error", "response_text": ""})
        sess._maybe_continue_goal(None)  # None-safe (critic R6)
        self.assertTrue(sess._inbox.empty())
        self.assertEqual(sess._goal_mgr.state.turns_used, 0)
        self.assertEqual(_goal_events(emitted), [])

    def test_clear_during_judge_discards_stale_verdict(self) -> None:
        """Critic R1: a /goal clear that lands while the judge is in flight
        must win — the stale verdict is discarded and nothing is enqueued."""
        sess, _ = _make_session()

        def judge_that_races_a_clear(system: str, user: str) -> str:
            # Simulates the control plane clearing the goal mid-judge (the
            # judge runs OUTSIDE the lock, so this interleaving is real).
            with sess._lock:
                sess._goal_mgr.clear()
            return '{"verdict": "continue", "reason": "stale"}'

        _set_goal(sess, judge=judge_that_races_a_clear)
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        self.assertTrue(sess._inbox.empty())
        self.assertFalse(sess._goal_mgr.has_goal())

    def test_replaced_goal_during_judge_discards_stale_verdict(self) -> None:
        sess, _ = _make_session()

        def judge_that_races_a_new_set(system: str, user: str) -> str:
            with sess._lock:
                sess._goal_mgr.set("a brand new goal")
            return '{"verdict": "done", "reason": "stale done"}'

        _set_goal(sess, judge=judge_that_races_a_new_set)
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        # The stale DONE must not mark the NEW goal done.
        self.assertEqual(sess._goal_mgr.state.status, "active")
        self.assertEqual(sess._goal_mgr.state.goal, "a brand new goal")
        self.assertEqual(sess._goal_mgr.state.turns_used, 0)
        self.assertTrue(sess._inbox.empty())

    def test_judge_timeout_parks_without_continuation(self) -> None:
        """Critic R2: a hung evaluator parks the loop — goal stays active,
        no continuation, and the worker returns promptly."""
        sess, emitted = _make_session()

        def hung_judge(system: str, user: str) -> str:
            raise GoalJudgeTimeout("evaluator exceeded 30s")

        _set_goal(sess, judge=hung_judge)
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        self.assertTrue(sess._inbox.empty())
        self.assertEqual(sess._goal_mgr.state.status, "active")
        events = _goal_events(emitted)
        self.assertTrue(events and "timed out" in events[-1]["message"])

    def test_concurrent_clear_thread_never_yields_continuation(self) -> None:
        """True two-thread race: worker judging while the control plane
        clears. Whatever the interleaving, no continuation may survive a
        clear."""
        sess, _ = _make_session()
        judge_entered = threading.Event()
        release_judge = threading.Event()

        def blocking_judge(system: str, user: str) -> str:
            judge_entered.set()
            release_judge.wait(timeout=5)
            return '{"verdict": "continue", "reason": "late"}'

        _set_goal(sess, judge=blocking_judge)
        worker = threading.Thread(
            target=sess._maybe_continue_goal,
            args=({"subtype": "success", "response_text": "wip"},),
        )
        worker.start()
        self.assertTrue(judge_entered.wait(timeout=5))
        with sess._lock:  # the control-plane path
            sess._goal_mgr.clear()
        release_judge.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(sess._inbox.empty())


# ── worker routing of continuation items ───────────────────────────────


class TestWorkerGoalRouting(unittest.TestCase):
    def test_goal_item_runs_internal_and_reevaluates(self) -> None:
        """Continuations run with internal-turn semantics (no UPS hooks, no
        ultracode reminder, no odometer tick — critic R3) and re-enter the
        goal hook so the loop keeps going."""
        sess, _ = _make_session()
        _set_goal(sess)  # the worker's staleness check drops items for dead goals
        calls: list[tuple] = []

        def fake_run_turn(prompt, btw=False, internal=False):
            calls.append(("turn", prompt, btw, internal))
            return {"subtype": "success", "response_text": "ok"}

        sess._run_turn = fake_run_turn  # type: ignore[method-assign]
        sess._maybe_continue_goal = lambda outcome: calls.append(("goal", outcome))  # type: ignore[method-assign]
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]

        sess._inbox.put({"__goal__": True, "content": "[Continuing…]"})
        sess._inbox.put(_SHUTDOWN)
        sess._run_worker()

        self.assertEqual(calls[0], ("turn", "[Continuing…]", False, True))
        self.assertEqual(calls[1][0], "goal")
        self.assertEqual(calls[1][1], {"subtype": "success", "response_text": "ok"})

    def test_real_turn_feeds_outcome_to_goal_hook(self) -> None:
        sess, _ = _make_session()
        calls: list[tuple] = []
        sess._run_turn = lambda prompt, btw=False, internal=False: (  # type: ignore[method-assign]
            calls.append(("turn", prompt, internal)) or {"subtype": "success", "response_text": "hi"}
        )
        sess._maybe_continue_goal = lambda outcome: calls.append(("goal", outcome))  # type: ignore[method-assign]
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]

        sess._inbox.put("a user prompt")
        sess._inbox.put(_SHUTDOWN)
        sess._run_worker()

        self.assertEqual(calls[0], ("turn", "a user prompt", False))
        self.assertEqual(calls[1], ("goal", {"subtype": "success", "response_text": "hi"}))

    def test_queued_continuation_dropped_after_clear(self) -> None:
        """A /goal clear that lands while a continuation sits queued must
        drop it — no model turn runs for a dead goal (worker staleness
        check; hermes clears pending synthetic continuations likewise)."""
        sess, _ = _make_session()
        _set_goal(sess)
        turns: list = []
        sess._run_turn = lambda *a, **k: (  # type: ignore[method-assign]
            turns.append(a) or {"subtype": "success", "response_text": "x"}
        )
        sess._maybe_continue_goal = lambda outcome: None  # type: ignore[method-assign]
        sess._deliver_task_notifications = lambda: False  # type: ignore[method-assign]

        sess._inbox.put({"__goal__": True, "content": "[Continuing…]"})
        with sess._lock:
            sess._goal_mgr.clear()
        sess._inbox.put(_SHUTDOWN)
        sess._run_worker()

        self.assertEqual(turns, [])

    def test_internal_notification_turns_do_not_hit_goal_hook(self) -> None:
        """Critic R5: judging a goal against a background-task recap would
        entangle two self-driving loops — _deliver_task_notifications never
        routes into _maybe_continue_goal."""
        sess, _ = _make_session()
        hook_calls: list = []
        sess._maybe_continue_goal = lambda outcome: hook_calls.append(outcome)  # type: ignore[method-assign]
        sess._run_turn = lambda *a, **k: {"subtype": "success", "response_text": "recap"}  # type: ignore[method-assign]

        with patch(
            "src.utils.message_queue_manager.drain_pending_notifications",
            return_value=[SimpleNamespace(value="<task-notification id='t1'/>")],
        ):
            delivered = sess._deliver_task_notifications()
        self.assertTrue(delivered)
        self.assertEqual(hook_calls, [])


# ── persistence: save + resume ─────────────────────────────────────────


class TestGoalPersistence(unittest.TestCase):
    def test_save_session_carries_goal_and_resume_restores_it(self) -> None:
        import src.server.agent_server as srv

        with __import__("tempfile").TemporaryDirectory() as td:
            tmp = __import__("pathlib").Path(td)
            with patch.object(srv, "_sessions_dir", return_value=tmp):
                sess, _ = _make_session()
                # A real-enough conversation for save/restore.
                from src.agent.conversation import Conversation

                conv = Conversation()
                conv.add_user_message("kick")
                sess.session = SimpleNamespace(conversation=conv)
                sess._save_session = _AgentSession._save_session.__get__(sess)
                _set_goal(sess, "finish the port")
                sess._goal_mgr.state.turns_used = 4
                sess._save_session()

                saved = json.loads((tmp / "goal-sess.json").read_text())
                self.assertEqual(saved["goal"]["goal"], "finish the port")
                self.assertEqual(saved["goal"]["turns_used"], 4)

                # Fresh session resumes it with counters reset (CC §Resume).
                sess2, emitted2 = _make_session()
                sess2.session = SimpleNamespace(conversation=Conversation())
                sess2._save_session = lambda: None  # type: ignore[method-assign]
                asyncio.run(sess2._handle_control_request({
                    "type": "control_request",
                    "request_id": "r9",
                    "request": {"subtype": "resume", "session_id": "goal-sess"},
                }))
                reply = _last_reply(emitted2)
                self.assertTrue(reply["ok"])
                self.assertIn("goal_notice", reply)
                self.assertTrue(sess2._goal_mgr.is_active())
                self.assertEqual(sess2._goal_mgr.state.goal, "finish the port")
                self.assertEqual(sess2._goal_mgr.state.turns_used, 0)

    def test_achieved_goal_not_restored(self) -> None:
        import src.server.agent_server as srv

        with __import__("tempfile").TemporaryDirectory() as td:
            tmp = __import__("pathlib").Path(td)
            with patch.object(srv, "_sessions_dir", return_value=tmp):
                sess, _ = _make_session()
                from src.agent.conversation import Conversation

                conv = Conversation()
                conv.add_user_message("kick")
                sess.session = SimpleNamespace(conversation=conv)
                sess._save_session = _AgentSession._save_session.__get__(sess)
                _set_goal(sess, "done goal")
                sess._goal_mgr.mark_done("achieved")
                sess._save_session()

                sess2, emitted2 = _make_session()
                sess2.session = SimpleNamespace(conversation=Conversation())
                sess2._save_session = lambda: None  # type: ignore[method-assign]
                asyncio.run(sess2._handle_control_request({
                    "type": "control_request",
                    "request_id": "r9",
                    "request": {"subtype": "resume", "session_id": "goal-sess"},
                }))
                reply = _last_reply(emitted2)
                self.assertTrue(reply["ok"])
                self.assertNotIn("goal_notice", reply)
                self.assertFalse(
                    sess2._goal_mgr is not None and sess2._goal_mgr.is_active()
                )


# ── indicator snapshot feed (TUI "◎ /goal active (14s)" line) ──────────


class TestGoalSnapshotFeed(unittest.TestCase):
    """Every goal-state carrier (control replies + goal_status events) must
    ship the compact snapshot the TUI indicator renders from — with
    ``created_at`` so the client owns a correct ticking elapsed display."""

    def test_set_reply_carries_active_snapshot(self) -> None:
        sess, emitted = _make_session()
        with patch.object(_AgentSession, "_goal_set_gate", return_value=None):
            _control(sess, "goal", arg="ship the feature")
        snap = _last_reply(emitted)["goal"]
        self.assertEqual(snap["status"], "active")
        self.assertEqual(snap["goal"], "ship the feature")
        self.assertGreater(snap["created_at"], 0)
        self.assertEqual(snap["turns_used"], 0)
        self.assertGreater(snap["max_turns"], 0)

    def test_clear_reply_carries_none(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "goal", arg="clear")
        reply = _last_reply(emitted)
        self.assertIn("goal", reply)
        self.assertIsNone(reply["goal"])

    def test_pause_and_status_replies_carry_paused_snapshot(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "goal", arg="pause")
        self.assertEqual(_last_reply(emitted)["goal"]["status"], "paused")
        _control(sess, "goal", arg="")
        self.assertEqual(_last_reply(emitted)["goal"]["status"], "paused")

    def test_subgoal_reply_carries_snapshot(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "subgoal", arg="also update docs")
        self.assertEqual(_last_reply(emitted)["goal"]["status"], "active")

    def test_interrupt_pause_event_carries_paused_snapshot(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess)
        _control(sess, "interrupt")
        event = _goal_events(emitted)[-1]
        self.assertEqual(event["goal"]["status"], "paused")

    def test_continue_event_carries_snapshot_with_turn_odometer(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "continue", "reason": "tests still red"}'
        ))
        sess._maybe_continue_goal({"subtype": "success", "response_text": "wip"})
        event = _goal_events(emitted)[-1]
        self.assertEqual(event["goal"]["status"], "active")
        self.assertEqual(event["goal"]["turns_used"], 1)

    def test_done_event_carries_none_snapshot(self) -> None:
        sess, emitted = _make_session()
        _set_goal(sess, judge=_judge_returning(
            '{"verdict": "done", "reason": "all pass"}'
        ))
        sess._maybe_continue_goal({"subtype": "success", "response_text": "ok"})
        event = _goal_events(emitted)[-1]
        self.assertIn("goal", event)
        self.assertIsNone(event["goal"])

    def test_restore_event_carries_active_snapshot(self) -> None:
        import src.server.agent_server as srv

        with __import__("tempfile").TemporaryDirectory() as td:
            tmp = __import__("pathlib").Path(td)
            with patch.object(srv, "_sessions_dir", return_value=tmp):
                sess, _ = _make_session()
                from src.agent.conversation import Conversation

                conv = Conversation()
                conv.add_user_message("kick")
                sess.session = SimpleNamespace(conversation=conv)
                sess._save_session = _AgentSession._save_session.__get__(sess)
                _set_goal(sess, "finish the port")
                sess._save_session()

                sess2, emitted2 = _make_session()
                sess2.session = SimpleNamespace(conversation=Conversation())
                sess2._save_session = lambda: None  # type: ignore[method-assign]
                asyncio.run(sess2._handle_control_request({
                    "type": "control_request",
                    "request_id": "r9",
                    "request": {"subtype": "resume", "session_id": "goal-sess"},
                }))
                event = _goal_events(emitted2)[-1]
                self.assertEqual(event["goal"]["status"], "active")
                self.assertEqual(event["goal"]["goal"], "finish the port")
                self.assertGreater(event["goal"]["created_at"], 0)


class TestUsageTokenTotal(unittest.TestCase):
    def test_sums_model_usage(self) -> None:
        snap = {"model_usage": {
            "m1": {"input_tokens": 10, "output_tokens": 5},
            "m2": {"input_tokens": 1, "output_tokens": 2},
        }}
        self.assertEqual(_AgentSession._usage_token_total(snap), 18)

    def test_junk_safe(self) -> None:
        self.assertEqual(_AgentSession._usage_token_total({}), 0)
        self.assertEqual(_AgentSession._usage_token_total({"model_usage": None}), 0)


if __name__ == "__main__":
    unittest.main()
