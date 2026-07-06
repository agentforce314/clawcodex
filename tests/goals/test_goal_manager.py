"""Unit coverage for the /goal core: GoalState, judge parsing, GoalManager
transitions, evaluation matrix, continuation prompts, evidence collection,
and the shared /goal · /subgoal command grammar."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.goals.command import run_goal_command, run_subgoal_command
from src.goals.goals import (
    CONTINUATION_PROMPT_TEMPLATE,
    GOAL_CLEAR_ALIASES,
    GOAL_CONDITION_MAX_CHARS,
    GoalManager,
    GoalState,
    MAX_CONSECUTIVE_PARSE_FAILURES,
    _parse_judge_response,
    collect_turn_evidence,
    judge_goal,
)


def _mgr(judge=None, **kwargs) -> GoalManager:
    return GoalManager("sess-1", judge=judge, **kwargs)


def _judge_returning(payload: str):
    return lambda system, user: payload


# ── judge reply parsing ────────────────────────────────────────────────


class TestParseJudgeResponse(unittest.TestCase):
    def test_clean_json_done(self) -> None:
        v, r, failed = _parse_judge_response(
            '{"verdict": "done", "reason": "tests pass"}'
        )
        self.assertEqual(v, "done")
        self.assertEqual(r, "tests pass")
        self.assertFalse(failed)

    def test_clean_json_continue(self) -> None:
        v, _, failed = _parse_judge_response(
            '{"verdict": "continue", "reason": "lint still failing"}'
        )
        self.assertEqual(v, "continue")
        self.assertFalse(failed)

    def test_json_in_markdown_fence(self) -> None:
        raw = '```json\n{"verdict": "done", "reason": "ok"}\n```'
        v, r, failed = _parse_judge_response(raw)
        self.assertEqual(v, "done")
        self.assertEqual(r, "ok")
        self.assertFalse(failed)

    def test_json_embedded_in_prose(self) -> None:
        raw = 'Sure! Here is my verdict: {"verdict": "continue", "reason": "wip"} hope that helps'
        v, r, failed = _parse_judge_response(raw)
        self.assertEqual(v, "continue")
        self.assertEqual(r, "wip")
        self.assertFalse(failed)

    def test_legacy_done_bool(self) -> None:
        v, _, failed = _parse_judge_response('{"done": true, "reason": "x"}')
        self.assertEqual(v, "done")
        self.assertFalse(failed)
        v, _, _ = _parse_judge_response('{"done": false, "reason": "x"}')
        self.assertEqual(v, "continue")

    def test_legacy_done_string_values(self) -> None:
        v, _, _ = _parse_judge_response('{"done": "yes", "reason": "x"}')
        self.assertEqual(v, "done")
        v, _, _ = _parse_judge_response('{"done": "no", "reason": "x"}')
        self.assertEqual(v, "continue")

    def test_unknown_verdict_falls_back_to_continue(self) -> None:
        v, _, failed = _parse_judge_response(
            '{"verdict": "maybe", "reason": "?"}'
        )
        self.assertEqual(v, "continue")
        self.assertFalse(failed)

    def test_empty_is_parse_failure(self) -> None:
        v, _, failed = _parse_judge_response("")
        self.assertEqual(v, "continue")
        self.assertTrue(failed)

    def test_prose_is_parse_failure(self) -> None:
        v, _, failed = _parse_judge_response("The goal seems done to me!")
        self.assertEqual(v, "continue")
        self.assertTrue(failed)

    def test_missing_reason_defaults(self) -> None:
        _, r, _ = _parse_judge_response('{"verdict": "done"}')
        self.assertEqual(r, "no reason provided")


# ── judge_goal orchestration ───────────────────────────────────────────


class TestJudgeGoal(unittest.TestCase):
    def test_empty_goal_skipped(self) -> None:
        v, _, failed = judge_goal("", "output", judge=_judge_returning("{}"))
        self.assertEqual(v, "skipped")
        self.assertFalse(failed)

    def test_empty_evidence_continues(self) -> None:
        v, r, failed = judge_goal("do x", "  ", judge=_judge_returning("{}"))
        self.assertEqual(v, "continue")
        self.assertIn("empty response", r)
        self.assertFalse(failed)

    def test_no_judge_continues(self) -> None:
        v, r, failed = judge_goal("do x", "output", judge=None)
        self.assertEqual(v, "continue")
        self.assertIn("no evaluator", r)
        self.assertFalse(failed)

    def test_judge_exception_fails_open_not_parse_failure(self) -> None:
        def boom(system, user):
            raise RuntimeError("api down")

        v, r, failed = judge_goal("do x", "output", judge=boom)
        self.assertEqual(v, "continue")
        self.assertIn("judge error", r)
        self.assertFalse(failed)

    def test_subgoals_reach_the_prompt(self) -> None:
        seen: dict[str, str] = {}

        def capture(system, user):
            seen["user"] = user
            return '{"verdict": "continue", "reason": "wip"}'

        judge_goal("main goal", "output", judge=capture, subgoals=["extra one"])
        self.assertIn("extra one", seen["user"])
        self.assertIn("every additional criterion", seen["user"])

    def test_goal_and_evidence_truncated(self) -> None:
        seen: dict[str, str] = {}

        def capture(system, user):
            seen["user"] = user
            return '{"verdict": "continue", "reason": "wip"}'

        judge_goal("g" * 5000, "e" * 10000, judge=capture)
        self.assertLess(len(seen["user"]), 8000)


# ── build_judge_callable ───────────────────────────────────────────────


class TestBuildJudgeCallable(unittest.TestCase):
    def test_returns_content_and_pins_no_model_for_non_anthropic(self) -> None:
        from src.goals.goals import build_judge_callable

        calls: dict = {}

        class P:
            def chat(self, messages, **kwargs):
                calls.update(kwargs)
                return SimpleNamespace(content=' {"verdict":"done","reason":"x"} ')

        out = build_judge_callable(P())("sys", "user")
        self.assertEqual(out, '{"verdict":"done","reason":"x"}')
        self.assertNotIn("model", calls)  # session-model fallback
        self.assertEqual(calls["system"], "sys")

    def test_hung_call_raises_timeout_on_daemon_thread(self) -> None:
        import threading

        from src.goals.goals import GoalJudgeTimeout, build_judge_callable

        release = threading.Event()

        class Hung:
            def chat(self, messages, **kwargs):
                release.wait(10)
                return SimpleNamespace(content="late")

        judge = build_judge_callable(Hung(), timeout_s=0.2)
        with self.assertRaises(GoalJudgeTimeout):
            judge("sys", "user")
        release.set()  # unblock the daemon thread

    def test_provider_error_propagates_for_fail_open(self) -> None:
        from src.goals.goals import build_judge_callable

        class Boom:
            def chat(self, messages, **kwargs):
                raise RuntimeError("api down")

        with self.assertRaises(RuntimeError):
            build_judge_callable(Boom())("sys", "user")
        # judge_goal turns that into a fail-open continue:
        v, r, failed = judge_goal(
            "goal", "evidence", judge=build_judge_callable(Boom())
        )
        self.assertEqual(v, "continue")
        self.assertFalse(failed)


# ── GoalManager state machine ──────────────────────────────────────────


class TestGoalManager(unittest.TestCase):
    def test_initial_no_goal(self) -> None:
        m = _mgr()
        self.assertFalse(m.is_active())
        self.assertFalse(m.has_goal())
        self.assertIn("No active goal", m.status_text())

    def test_set_then_status(self) -> None:
        m = _mgr()
        st = m.set("ship the feature")
        self.assertEqual(st.status, "active")
        self.assertTrue(m.is_active())
        self.assertIn("ship the feature", m.status_text())
        self.assertIn("Turns evaluated: 0/", m.status_text())

    def test_set_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            _mgr().set("   ")

    def test_set_rejects_over_cap(self) -> None:
        with self.assertRaises(ValueError):
            _mgr().set("x" * (GOAL_CONDITION_MAX_CHARS + 1))

    def test_set_replaces_existing(self) -> None:
        m = _mgr()
        m.set("first")
        m.state.turns_used = 5
        st = m.set("second")
        self.assertEqual(st.goal, "second")
        self.assertEqual(st.turns_used, 0)

    def test_pause_resume_resets_budget(self) -> None:
        m = _mgr()
        m.set("goal")
        m.state.turns_used = 7
        m.pause()
        self.assertFalse(m.is_active())
        self.assertTrue(m.has_goal())
        st = m.resume()
        self.assertEqual(st.status, "active")
        self.assertEqual(st.turns_used, 0)

    def test_pause_after_done_is_noop(self) -> None:
        m = _mgr()
        m.set("goal")
        m.mark_done("achieved")
        self.assertIsNone(m.pause())
        self.assertIsNone(m.resume())

    def test_clear(self) -> None:
        m = _mgr()
        m.set("goal")
        self.assertTrue(m.clear())
        self.assertFalse(m.has_goal())
        self.assertFalse(m.clear())

    def test_achieved_status_record(self) -> None:
        m = _mgr()
        m.set("goal")
        m.mark_done("evidence shown")
        text = m.status_text()
        self.assertIn("✓ Goal achieved", text)
        self.assertIn("evidence shown", text)


# ── evaluation matrix ──────────────────────────────────────────────────


class TestEvaluateAfterTurn(unittest.TestCase):
    def test_inactive(self) -> None:
        d = _mgr().evaluate_after_turn("output")
        self.assertEqual(d["verdict"], "inactive")
        self.assertFalse(d["should_continue"])

    def test_done_marks_and_stops(self) -> None:
        m = _mgr(judge=_judge_returning('{"verdict": "done", "reason": "all pass"}'))
        m.set("goal")
        d = m.evaluate_after_turn("tests: 10 passed")
        self.assertEqual(d["verdict"], "done")
        self.assertFalse(d["should_continue"])
        self.assertEqual(m.state.status, "done")
        self.assertIn("✓ Goal achieved", d["message"])

    def test_continue_under_budget(self) -> None:
        m = _mgr(judge=_judge_returning('{"verdict": "continue", "reason": "2 tests failing"}'))
        m.set("goal")
        d = m.evaluate_after_turn("wip")
        self.assertTrue(d["should_continue"])
        self.assertIn("Goal: goal", d["continuation_prompt"])
        self.assertIn("2 tests failing", d["continuation_prompt"])
        self.assertEqual(m.state.turns_used, 1)

    def test_budget_exhaustion_pauses(self) -> None:
        m = _mgr(judge=_judge_returning('{"verdict": "continue", "reason": "wip"}'))
        m.set("goal", max_turns=2)
        d1 = m.evaluate_after_turn("wip")
        self.assertTrue(d1["should_continue"])
        d2 = m.evaluate_after_turn("wip")
        self.assertFalse(d2["should_continue"])
        self.assertEqual(d2["status"], "paused")
        self.assertIn("/goal resume", d2["message"])

    def test_parse_failures_pause_after_threshold(self) -> None:
        m = _mgr(judge=_judge_returning("not json at all"))
        m.set("goal", max_turns=50)
        for i in range(MAX_CONSECUTIVE_PARSE_FAILURES - 1):
            d = m.evaluate_after_turn("wip")
            self.assertTrue(d["should_continue"], f"iteration {i}")
        d = m.evaluate_after_turn("wip")
        self.assertFalse(d["should_continue"])
        self.assertEqual(d["status"], "paused")
        self.assertIn("evaluator", d["message"])

    def test_parse_failure_counter_resets_on_good_reply(self) -> None:
        replies = iter([
            "junk", "junk",
            '{"verdict": "continue", "reason": "ok"}',
            "junk", "junk",
        ])
        m = _mgr(judge=lambda s, u: next(replies))
        m.set("goal", max_turns=50)
        for _ in range(5):
            d = m.evaluate_after_turn("wip")
        # Never hit 3 consecutive failures — still active.
        self.assertTrue(d["should_continue"])
        self.assertEqual(m.state.status, "active")

    def test_transport_errors_do_not_count_as_parse_failures(self) -> None:
        def boom(s, u):
            raise RuntimeError("network")

        m = _mgr(judge=boom)
        m.set("goal", max_turns=50)
        for _ in range(MAX_CONSECUTIVE_PARSE_FAILURES + 2):
            d = m.evaluate_after_turn("wip")
        self.assertTrue(d["should_continue"])
        self.assertEqual(m.state.status, "active")

    def test_token_spend_tracked_from_baseline(self) -> None:
        m = _mgr(judge=_judge_returning('{"verdict": "continue", "reason": "wip"}'))
        m.set("goal", baseline_tokens=1000, baseline_cost_usd=0.10)
        m.evaluate_after_turn("wip", tokens_now=1500, cost_now_usd=0.15)
        self.assertEqual(m.state.spent_tokens, 500)
        self.assertAlmostEqual(m.state.spent_cost_usd, 0.05, places=6)


# ── continuation prompts ───────────────────────────────────────────────


class TestContinuationPrompt(unittest.TestCase):
    def test_plain_shape(self) -> None:
        m = _mgr()
        m.set("write the docs")
        p = m.next_continuation_prompt("intro missing")
        self.assertEqual(
            p,
            CONTINUATION_PROMPT_TEMPLATE.format(
                goal="write the docs", reason="intro missing"
            ),
        )

    def test_subgoals_shape(self) -> None:
        m = _mgr()
        m.set("write the docs")
        m.add_subgoal("add examples")
        p = m.next_continuation_prompt("wip")
        self.assertIn("Additional criteria", p)
        self.assertIn("- 1. add examples", p)

    def test_none_when_inactive(self) -> None:
        m = _mgr()
        self.assertIsNone(m.next_continuation_prompt("x"))


# ── subgoal ops ────────────────────────────────────────────────────────


class TestSubgoals(unittest.TestCase):
    def test_add_remove_clear(self) -> None:
        m = _mgr()
        m.set("goal")
        m.add_subgoal("one")
        m.add_subgoal("two")
        self.assertEqual(m.remove_subgoal(1), "one")
        self.assertEqual(m.clear_subgoals(), 1)

    def test_requires_goal(self) -> None:
        m = _mgr()
        with self.assertRaises(RuntimeError):
            m.add_subgoal("x")

    def test_remove_out_of_range(self) -> None:
        m = _mgr()
        m.set("goal")
        with self.assertRaises(IndexError):
            m.remove_subgoal(1)

    def test_empty_rejected(self) -> None:
        m = _mgr()
        m.set("goal")
        with self.assertRaises(ValueError):
            m.add_subgoal("  ")


# ── persistence bridge ─────────────────────────────────────────────────


class TestPersistence(unittest.TestCase):
    def test_round_trip(self) -> None:
        m = _mgr()
        m.set("goal text")
        m.add_subgoal("crit")
        m.state.turns_used = 3
        data = m.state.to_dict()

        m2 = _mgr()
        st = m2.restore(data, reset_counters=False)
        self.assertIsNotNone(st)
        self.assertEqual(st.goal, "goal text")
        self.assertEqual(st.turns_used, 3)
        self.assertEqual(st.subgoals, ["crit"])

    def test_restore_resets_counters(self) -> None:
        m = _mgr()
        m.set("goal")
        m.state.turns_used = 9
        m.state.spent_tokens = 12345
        data = m.state.to_dict()

        m2 = _mgr()
        st = m2.restore(data)  # reset_counters=True default
        self.assertEqual(st.turns_used, 0)
        self.assertEqual(st.spent_tokens, 0)
        self.assertTrue(m2.is_active())

    def test_only_active_goals_restore(self) -> None:
        m = _mgr()
        m.set("goal")
        m.mark_done("done")
        data = m.state.to_dict()
        self.assertIsNone(_mgr().restore(data))

        m3 = _mgr()
        m3.set("goal2")
        m3.pause()
        self.assertIsNone(_mgr().restore(m3.state.to_dict()))

    def test_corrupt_record_ignored(self) -> None:
        self.assertIsNone(_mgr().restore({"goal": ""}))
        self.assertIsNone(_mgr().restore({"status": []}))  # type: ignore[dict-item]


# ── evidence collection ────────────────────────────────────────────────


def _m(role: str, content, is_meta: bool = False) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, isMeta=is_meta)


class TestCollectTurnEvidence(unittest.TestCase):
    def test_collects_since_last_user_prompt(self) -> None:
        msgs = [
            _m("user", "old prompt"),
            _m("assistant", "old answer"),
            _m("user", "run the tests"),
            _m("assistant", [
                {"type": "text", "text": "Running tests."},
                {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}},
            ]),
            _m("user", [{
                "type": "tool_result", "tool_use_id": "t1",
                "content": "10 passed, 0 failed",
            }]),
            _m("assistant", "All tests pass."),
        ]
        ev = collect_turn_evidence(msgs)
        self.assertIn("All tests pass.", ev)
        self.assertIn("10 passed, 0 failed", ev)
        self.assertIn("[called tool: Bash]", ev)
        self.assertNotIn("old answer", ev)

    def test_meta_user_messages_do_not_end_walk(self) -> None:
        msgs = [
            _m("user", "prompt"),
            _m("assistant", "answer one"),
            _m("user", "<system-reminder>ctx</system-reminder>", is_meta=True),
            _m("assistant", "answer two"),
        ]
        ev = collect_turn_evidence(msgs)
        self.assertIn("answer one", ev)
        self.assertIn("answer two", ev)

    def test_char_budget_keeps_newest(self) -> None:
        msgs = [
            _m("user", "prompt"),
            _m("assistant", "A" * 5000),
            _m("assistant", "FINAL ANSWER"),
        ]
        ev = collect_turn_evidence(msgs, limit_chars=500)
        self.assertIn("FINAL ANSWER", ev)
        self.assertLessEqual(len(ev), 600)

    def test_empty_and_junk_safe(self) -> None:
        self.assertEqual(collect_turn_evidence([]), "")
        self.assertEqual(collect_turn_evidence([SimpleNamespace()]), "")


# ── shared command grammar ─────────────────────────────────────────────


class TestGoalCommand(unittest.TestCase):
    def test_bare_status(self) -> None:
        r = run_goal_command(_mgr(), "")
        self.assertTrue(r.ok)
        self.assertIn("No active goal", r.text)
        self.assertIsNone(r.kickoff)

    def test_set_returns_kickoff_and_notice(self) -> None:
        m = _mgr()
        r = run_goal_command(m, "make the build green")
        self.assertTrue(r.ok)
        self.assertEqual(r.kickoff, "make the build green")
        self.assertIn("Goal set", r.notice)
        self.assertTrue(m.is_active())

    def test_all_clear_aliases(self) -> None:
        for alias in sorted(GOAL_CLEAR_ALIASES):
            m = _mgr()
            run_goal_command(m, "some goal")
            r = run_goal_command(m, alias)
            self.assertTrue(r.ok, alias)
            self.assertIn("cleared", r.text.lower(), alias)
            self.assertFalse(m.has_goal(), alias)

    def test_pause_and_resume(self) -> None:
        m = _mgr()
        run_goal_command(m, "some goal")
        r = run_goal_command(m, "pause")
        self.assertIn("paused", r.text.lower())
        r = run_goal_command(m, "resume")
        self.assertIn("resumed", r.text.lower())
        self.assertTrue(m.is_active())

    def test_set_gate_blocks_set_only(self) -> None:
        m = _mgr()
        gate = lambda: "/goal requires a trusted workspace."  # noqa: E731
        r = run_goal_command(m, "do things", set_gate=gate)
        self.assertFalse(r.ok)
        self.assertIn("trusted workspace", r.text)
        self.assertFalse(m.has_goal())
        # status/clear still allowed under a closed gate
        r = run_goal_command(m, "", set_gate=gate)
        self.assertTrue(r.ok)
        r = run_goal_command(m, "clear", set_gate=gate)
        self.assertTrue(r.ok)

    def test_case_insensitive_subcommands(self) -> None:
        m = _mgr()
        run_goal_command(m, "goal")
        r = run_goal_command(m, "CLEAR")
        self.assertIn("cleared", r.text.lower())

    def test_condition_cap_surfaced(self) -> None:
        r = run_goal_command(_mgr(), "x" * (GOAL_CONDITION_MAX_CHARS + 1))
        self.assertFalse(r.ok)
        self.assertIn("Invalid goal", r.text)


class TestSubgoalCommand(unittest.TestCase):
    def test_requires_goal(self) -> None:
        r = run_subgoal_command(_mgr(), "extra")
        self.assertFalse(r.ok)
        self.assertIn("No active goal", r.text)

    def test_add_list_remove_clear(self) -> None:
        m = _mgr()
        m.set("goal")
        r = run_subgoal_command(m, "first criterion")
        self.assertTrue(r.ok)
        self.assertIn("Added subgoal 1", r.text)
        r = run_subgoal_command(m, "")
        self.assertIn("first criterion", r.text)
        r = run_subgoal_command(m, "remove 1")
        self.assertIn("Removed subgoal 1", r.text)
        run_subgoal_command(m, "again")
        r = run_subgoal_command(m, "clear")
        self.assertIn("Cleared 1 subgoal", r.text)

    def test_remove_validation(self) -> None:
        m = _mgr()
        m.set("goal")
        self.assertFalse(run_subgoal_command(m, "remove").ok)
        self.assertFalse(run_subgoal_command(m, "remove x").ok)
        self.assertFalse(run_subgoal_command(m, "remove 4").ok)


if __name__ == "__main__":
    unittest.main()
