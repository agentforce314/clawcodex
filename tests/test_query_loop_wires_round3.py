"""ch05 round-3 acceptance tests: the five loop wires.

Stop hooks at the clean exit (G1), token budget (G2), /clear+compact
section resets (G3), post-compaction mark (G4), continuation nudge (G5).
Plan: my-docs/python-port-improvement-round-3/ch05-agent-loop-round3-plan.md.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src.bootstrap.state import (
    consume_post_compaction,
    get_budget_continuation_count,
    reset_state_for_tests,
)


def get_pending_post_compaction() -> bool:
    # Read the module attribute dynamically: reset_state_for_tests REBINDS
    # _STATE, so an imported reference goes stale.
    from src.bootstrap import state as _bs

    return _bs._STATE.pending_post_compaction
from src.providers.base import ChatResponse
from src.query.continuation_nudge import (
    EXHAUSTIVE_AUDIT_NUDGE,
    MAX_CONTINUATION_NUDGES,
    detect_continuation_signal,
    requests_exhaustive_results,
)
from src.query.query import QueryParams, run_query
from src.query.stop_hooks import StopHookResult
from src.state.cache_state import (
    get_beta_header_latches,
    reset_for_test_only as reset_cache_state_for_tests,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


@pytest.fixture(autouse=True)
def _reset():
    reset_state_for_tests()
    try:
        reset_cache_state_for_tests()
    except Exception:
        pass
    yield
    reset_state_for_tests()
    try:
        reset_cache_state_for_tests()
    except Exception:
        pass


def _run(coro):
    return asyncio.run(coro)


def _completion(content="Done. The task is complete.") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="claude-test",
        usage={"input_tokens": 7, "output_tokens": 3},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _provider(responses):
    provider = mock.MagicMock()
    provider.model = "claude-test"
    provider.chat_stream_response.side_effect = NotImplementedError()
    seq = list(responses)

    def chat(*a, **k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    provider.chat.side_effect = chat
    return provider


def _params(workspace: Path, provider, **kw) -> QueryParams:
    registry = build_default_registry()
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=kw.pop(
            "tool_use_context", ToolContext(workspace_root=workspace)
        ),
        provider=provider,
        abort_controller=AbortController(),
        max_turns=8,
        **kw,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


# ---------------------------------------------------------------------------
# G1 — stop hooks
# ---------------------------------------------------------------------------


class TestStopHooks(_Base):
    def test_prevent_continuation_terminal(self):
        async def fake_stream(*a, **k):
            yield StopHookResult(prevent_continuation=True)

        with mock.patch(
            "src.query.query.handle_stop_hooks_streaming", fake_stream
        ):
            _msgs, terminal = _run(
                run_query(_params(self.workspace, _provider([_completion()])))
            )
        self.assertEqual(terminal.reason, "stop_hook_prevented")

    def test_blocking_errors_retry_once_with_flag(self):
        calls: list = []

        async def fake_stream(messages, assistants, sp, ctx, source, active, *a, **k):
            calls.append(active)
            if len(calls) == 1:
                yield StopHookResult(
                    blocking_errors=[
                        UserMessage(content="Linter found 3 errors", isMeta=True)
                    ]
                )
            else:
                yield StopHookResult()

        with mock.patch(
            "src.query.query.handle_stop_hooks_streaming", fake_stream
        ):
            _msgs, terminal = _run(
                run_query(_params(self.workspace, _provider([_completion()])))
            )
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(len(calls), 2)          # one retry
        self.assertIsNone(calls[0])              # first run: not active
        self.assertTrue(calls[1])                # retry: stop_hook_active=True

    def test_api_error_skips_stop_hooks_fires_stop_failure(self):
        provider = mock.MagicMock()
        provider.model = "claude-test"
        provider.chat_stream_response.side_effect = NotImplementedError()
        err = _completion("")
        provider.chat.return_value = err

        stop_called = []
        failure_called = []

        async def fake_stream(*a, **k):
            stop_called.append(True)
            yield StopHookResult()

        async def fake_failure(*a, **k):
            failure_called.append(True)

        with mock.patch(
            "src.query.query.handle_stop_hooks_streaming", fake_stream
        ), mock.patch(
            "src.query.query._fire_stop_failure_hooks", fake_failure
        ), mock.patch(
            "src.query.query._call_model_sync",
            side_effect=_fake_api_error_response,
        ):
            _msgs, terminal = _run(
                run_query(_params(self.workspace, provider))
            )
        self.assertEqual(terminal.reason, "completed")
        self.assertFalse(stop_called)            # Stop hooks skipped
        self.assertTrue(failure_called)          # StopFailure fired

    def test_no_hooks_plain_completed_regression(self):
        _msgs, terminal = _run(
            run_query(_params(self.workspace, _provider([_completion()])))
        )
        self.assertEqual(terminal.reason, "completed")

    def test_max_tokens_exhausted_skips_stop_hooks(self):
        # Recovery-exhausted max-output-tokens message carries
        # isApiErrorMessage=False (real partial content) — the guard must
        # treat it as an error exit anyway, or a blocking Stop hook
        # re-opens the truncation spiral (escalate + 3 recoveries + hook
        # exec per cycle, unbounded).
        async def fake_model(**kwargs):
            from src.types.messages import AssistantMessage

            msg = AssistantMessage(content="partial answer, cut off mid-")
            msg._api_error = "max_output_tokens"
            return [msg], []

        stop_called: list = []
        failure_called: list = []

        async def fake_stream(*a, **k):
            # Block only on the first call so a future guard regression
            # FAILS at the asserts instead of hanging in the spiral.
            stop_called.append(True)
            if len(stop_called) == 1:
                yield StopHookResult(
                    blocking_errors=[
                        UserMessage(content="keep going", isMeta=True)
                    ]
                )
            else:
                yield StopHookResult()

        async def fake_failure(*a, **k):
            failure_called.append(True)

        with mock.patch(
            "src.query.query.handle_stop_hooks_streaming", fake_stream
        ), mock.patch(
            "src.query.query._fire_stop_failure_hooks", fake_failure
        ), mock.patch(
            "src.query.query._call_model_sync", side_effect=fake_model
        ):
            _msgs, terminal = _run(
                run_query(_params(self.workspace, _provider([_completion()])))
            )
        self.assertEqual(terminal.reason, "completed")
        self.assertFalse(stop_called)            # Stop hooks skipped
        self.assertTrue(failure_called)          # StopFailure fired

    def test_blocking_preserves_reactive_compact_guard(self):
        # Guard-5 pin: PTL → reactive compact → clean response → hook
        # blocks → PTL again must terminate prompt_too_long WITHOUT a
        # second compact attempt. If the stop_hook_blocking reconstruct
        # reset has_attempted_reactive_compact, this loops compacting
        # forever (TS query.ts:1375-1381 production incident).
        from src.services.compact.reactive_compact import ReactiveCompactResult

        seq = {"n": 0}

        async def fake_model(**kwargs):
            from src.types.messages import AssistantMessage

            seq["n"] += 1
            if seq["n"] in (1, 3):
                msg = AssistantMessage(content="")
                msg.isApiErrorMessage = True
                msg._api_error = "prompt_too_long"
                return [msg], []
            return [AssistantMessage(content="Here is my answer.")], []

        compact_calls: list = []

        async def fake_compact(**kwargs):
            compact_calls.append(True)
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="compacted summary", isMeta=True)],
                tokens_before=100,
                tokens_after=10,
            )

        hook_calls: list = []

        async def fake_stream(messages, assistants, sp, ctx, source, active, *a, **k):
            hook_calls.append(active)
            if len(hook_calls) == 1:
                yield StopHookResult(
                    blocking_errors=[
                        UserMessage(content="not done yet", isMeta=True)
                    ]
                )
            else:
                yield StopHookResult()

        with mock.patch(
            "src.query.query.handle_stop_hooks_streaming", fake_stream
        ), mock.patch(
            "src.services.compact.reactive_compact.reactive_compact",
            side_effect=fake_compact,
        ), mock.patch(
            "src.query.query._call_model_sync", side_effect=fake_model
        ):
            _msgs, terminal = _run(
                run_query(_params(self.workspace, _provider([_completion()])))
            )
        self.assertEqual(terminal.reason, "prompt_too_long")
        self.assertEqual(len(compact_calls), 1)  # no second compact
        self.assertEqual(len(hook_calls), 1)     # blocked once, then PTL exit


async def _fake_api_error_response(**kwargs):
    from src.types.messages import AssistantMessage

    msg = AssistantMessage(content="API error: boom")
    msg.isApiErrorMessage = True
    return [msg], []


# ---------------------------------------------------------------------------
# G2 — token budget
# ---------------------------------------------------------------------------


class TestTokenBudget(_Base):
    def _budgeted_params(self, provider, budget=10_000, **kw):
        return _params(self.workspace, provider, token_budget=budget, **kw)

    def _bump_output_tokens(self, n):
        # record_api_usage MERGES into the per-model accumulator;
        # add_to_total_cost_state REPLACES (its callers pre-merge).
        from src.cost_tracker import record_api_usage

        record_api_usage("claude-test", {"output_tokens": n})

    def test_snapshot_at_entry_makes_turn_tokens_turn_scoped(self):
        # Session already produced output BEFORE this query — without the
        # entry snapshot the budget math would see session-cumulative.
        self._bump_output_tokens(9_999)
        provider = _provider([_completion()])
        responses = {"n": 0}

        def chat(*a, **k):
            responses["n"] += 1
            # Each call produces tiny output; under 90% of budget the loop
            # would continue if turn tokens were computed correctly small,
            # but diminishing-returns will not trigger (deltas tiny but
            # continuation_count < 3 initially).
            self._bump_output_tokens(10)
            return _completion()

        provider.chat.side_effect = chat
        _msgs, terminal = _run(
            run_query(self._budgeted_params(provider, budget=100))
        )
        # turn tokens start at 0 (snapshot) and grow by ~10/turn; budget
        # 100 → continues until diminishing-returns stops it (3+ small
        # deltas) — NOT an instant stop from the 9,999 session tokens.
        self.assertEqual(terminal.reason, "completed")
        self.assertGreaterEqual(get_budget_continuation_count(), 3)

    def test_continues_past_three_while_under_threshold(self):
        # Anti-conflation: budget continuations are NOT capped at 3.
        provider = _provider([_completion()])
        turn = {"n": 0}

        def chat(*a, **k):
            turn["n"] += 1
            self._bump_output_tokens(1_000)   # healthy deltas, no diminishing
            return _completion()

        provider.chat.side_effect = chat
        _msgs, terminal = _run(
            run_query(
                self._budgeted_params(provider, budget=8_000)
            )
        )
        self.assertEqual(terminal.reason, "completed")
        # 1000/turn vs 8000 budget → 90% at 7200 → 7 turns continue
        self.assertGreater(get_budget_continuation_count(), 3)

    def test_nudge_appended_not_yielded(self):
        provider = _provider([_completion()])
        turn = {"n": 0}

        def chat(*a, **k):
            turn["n"] += 1
            self._bump_output_tokens(1_000)
            return _completion()

        provider.chat.side_effect = chat
        msgs, _terminal = _run(
            run_query(self._budgeted_params(provider, budget=3_000))
        )
        # Non-vacuity: the budget-continuation path actually ran.
        self.assertGreaterEqual(get_budget_continuation_count(), 1)
        # The nudge is appended to next-state messages, never yielded:
        # no isMeta UserMessage in the stream (this scenario yields no
        # legitimate user messages), and no nudge text (token_budget.py
        # phrasing: "token target" / "Keep working").
        for m in msgs:
            if isinstance(m, UserMessage):
                self.assertFalse(
                    getattr(m, "isMeta", False),
                    f"isMeta UserMessage leaked into the yield stream: {m}",
                )
        yielded_texts = [str(getattr(m, "content", "")).lower() for m in msgs]
        self.assertFalse(
            any(
                "token target" in t or "keep working" in t
                for t in yielded_texts
            ),
            f"budget nudge leaked into the yield stream: {yielded_texts}",
        )

    def test_subagent_stops_immediately(self):
        provider = _provider([_completion()])
        ctx = ToolContext(workspace_root=self.workspace)
        ctx.agent_id = "agent-123"
        _msgs, terminal = _run(
            run_query(
                self._budgeted_params(
                    provider, budget=1_000_000, tool_use_context=ctx
                )
            )
        )
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(get_budget_continuation_count(), 0)

    def test_no_budget_untouched_path(self):
        provider = _provider([_completion()])
        _msgs, terminal = _run(run_query(_params(self.workspace, provider)))
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(get_budget_continuation_count(), 0)

    def test_subagent_query_preserves_turn_snapshot(self):
        # A nested subagent query() (the Agent tool runs inside the main
        # turn's tool phase) must NOT re-snapshot — that would null the
        # main turn's budget, re-baseline the counter, and zero the
        # continuation count mid-turn.
        from src.bootstrap.state import (
            get_current_turn_token_budget,
            get_turn_output_tokens,
            increment_budget_continuation_count,
            snapshot_output_tokens_for_turn,
        )

        snapshot_output_tokens_for_turn(5_000)   # the main turn's snapshot
        increment_budget_continuation_count()
        self._bump_output_tokens(1_234)          # main-turn progress

        ctx = ToolContext(workspace_root=self.workspace)
        ctx.agent_id = "agent-nested"
        _msgs, terminal = _run(
            run_query(
                _params(self.workspace, _provider([_completion()]),
                        tool_use_context=ctx)
            )
        )
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(get_current_turn_token_budget(), 5_000)
        self.assertEqual(get_budget_continuation_count(), 1)
        self.assertGreaterEqual(get_turn_output_tokens(), 1_234)

    def test_sidechannel_source_preserves_turn_snapshot(self):
        from src.bootstrap.state import (
            get_current_turn_token_budget,
            snapshot_output_tokens_for_turn,
        )

        snapshot_output_tokens_for_turn(5_000)
        _msgs, terminal = _run(
            run_query(
                _params(self.workspace, _provider([_completion()]),
                        query_source="compact")
            )
        )
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(get_current_turn_token_budget(), 5_000)


# ---------------------------------------------------------------------------
# G1 — SubagentStop gate (stop_hooks.py)
# ---------------------------------------------------------------------------


class TestSubagentStopGate(_Base):
    def _drive(self, ctx):
        from src.query.stop_hooks import handle_stop_hooks_streaming

        async def run():
            items = []
            async for item in handle_stop_hooks_streaming(
                [], [], "", ctx, "agent", None
            ):
                items.append(item)
            return items

        return _run(run())

    def test_subagent_context_gates_on_subagent_stop(self):
        # A SubagentStop-only config must fire for subagent contexts —
        # gating on "Stop" alone silently disabled it (the executor
        # dispatches SubagentStop when subagent_id is set).
        gated_events: list = []
        ran: list = []

        def fake_has_hook(event, ctx):
            gated_events.append(event)
            return event == "SubagentStop"

        async def fake_execute(**kw):
            ran.append(kw.get("subagent_id"))
            if False:
                yield  # async generator

        with mock.patch(
            "src.hooks.hook_executor.has_hook_for_event", fake_has_hook
        ), mock.patch(
            "src.hooks.hook_executor.execute_stop_hooks", fake_execute
        ):
            ctx = ToolContext(workspace_root=self.workspace)
            ctx.agent_id = "agent-9"
            items = self._drive(ctx)
        self.assertEqual(gated_events, ["SubagentStop"])
        self.assertEqual(ran, ["agent-9"])
        self.assertIsInstance(items[-1], StopHookResult)

    def test_top_level_context_gates_on_stop(self):
        gated_events: list = []

        def fake_has_hook(event, ctx):
            gated_events.append(event)
            return False

        with mock.patch(
            "src.hooks.hook_executor.has_hook_for_event", fake_has_hook
        ):
            items = self._drive(ToolContext(workspace_root=self.workspace))
        self.assertEqual(gated_events, ["Stop"])
        self.assertIsInstance(items[-1], StopHookResult)


# ---------------------------------------------------------------------------
# G5 — continuation nudge
# ---------------------------------------------------------------------------


class TestContinuationNudge(_Base):
    def test_detector_matrix(self):
        self.assertTrue(
            detect_continuation_signal("So now I need to edit the file")
        )
        self.assertTrue(detect_continuation_signal("Let me run the tests"))
        self.assertTrue(detect_continuation_signal("Time to fix the bug"))
        # Completion markers win.
        self.assertFalse(
            detect_continuation_signal(
                "Let me run through what we did — the task is complete."
            )
        )
        # Short-only pattern: long "I'll ..." text does NOT match.
        long_text = (
            "I'll update the documentation later if needed, but for the "
            "moment here is a detailed explanation of the architecture "
            "and how the pieces fit together in this design."
        )
        self.assertFalse(detect_continuation_signal(long_text))
        self.assertTrue(detect_continuation_signal("I'll update the file"))

    def test_nudges_then_caps(self):
        provider = _provider([_completion("So now I need to edit the file")])
        msgs, terminal = _run(run_query(_params(self.workspace, provider)))
        self.assertEqual(terminal.reason, "completed")
        # The loop nudged MAX times (model kept signaling, never used tools)
        # then completed; assistant yields = MAX+1 model calls.
        from src.types.messages import AssistantMessage

        assistant_count = sum(
            1 for m in msgs if isinstance(m, AssistantMessage)
        )
        self.assertEqual(assistant_count, MAX_CONTINUATION_NUDGES + 1)

    def test_completion_text_no_nudge(self):
        provider = _provider([_completion("Done. Everything is complete.")])
        msgs, terminal = _run(run_query(_params(self.workspace, provider)))
        from src.types.messages import AssistantMessage

        self.assertEqual(
            sum(1 for m in msgs if isinstance(m, AssistantMessage)), 1
        )

    def test_exhaustive_request_forces_one_audit_pass(self):
        provider = _provider([
            _completion("I found one result and finished."),
            _completion("I audited all candidates and updated the result."),
        ])
        params = _params(self.workspace, provider)
        params.messages = [
            UserMessage(content="If there are multiple results, print them all.")
        ]
        msgs, terminal = _run(run_query(params))
        from src.types.messages import AssistantMessage

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(
            sum(1 for m in msgs if isinstance(m, AssistantMessage)), 2
        )

    def test_exhaustive_request_detector(self):
        self.assertTrue(requests_exhaustive_results("print them all"))
        self.assertTrue(requests_exhaustive_results("multiple winning moves"))
        self.assertFalse(requests_exhaustive_results("find the best move"))
        self.assertIn("MUST use a tool", EXHAUSTIVE_AUDIT_NUDGE)
        self.assertIn("state after each candidate action", EXHAUSTIVE_AUDIT_NUDGE)


# ---------------------------------------------------------------------------
# G3 + G4 — /clear resets; compaction marks
# ---------------------------------------------------------------------------


class TestClearAndCompactionState(_Base):
    def _latch_something(self):
        # get_beta_header_latches returns the LIVE latches object (the
        # fast_mode producer mutates it the same way, fast_mode.py:62-63).
        get_beta_header_latches().fast_mode_header_latched = True
        self.assertTrue(get_beta_header_latches().fast_mode_header_latched)

    def test_registry_clear_resets_sections_and_latches(self):
        from src.command_system.builtins import clear_command_call

        self._latch_something()
        context = mock.MagicMock()
        clear_command_call("", context)
        self.assertFalse(get_beta_header_latches().fast_mode_header_latched)
        self.assertFalse(get_pending_post_compaction())  # /clear ≠ compaction

    def test_compaction_success_marks_and_resets(self):
        from src.services.compact.compact import (
            CompactContext,
            compact_conversation,
        )
        from src.types.messages import AssistantMessage

        provider = mock.MagicMock()
        provider.model = "main"

        async def chat_async(*a, **k):
            return ChatResponse(
                content="A perfectly valid summary of the conversation.",
                model="summarize-model",
                usage={"input_tokens": 5, "output_tokens": 2},
                finish_reason="end_turn",
                tool_uses=None,
            )

        provider.chat_async = chat_async
        context = CompactContext(
            provider=provider,
            model="summarize-model",
            messages=[
                UserMessage(content="hello " * 60),
                AssistantMessage(content="world " * 60),
                UserMessage(content="more " * 60),
                AssistantMessage(content="words " * 60),
            ],
        )
        result = _run(compact_conversation(context))
        self.assertIsNotNone(result)
        self.assertTrue(get_pending_post_compaction())
        self.assertTrue(consume_post_compaction())  # consume-once works
        self.assertFalse(get_pending_post_compaction())


if __name__ == "__main__":
    unittest.main()
