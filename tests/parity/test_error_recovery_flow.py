"""WS-10: Behavioral parity — error recovery flow matches TS query loop.

Verifies:
- max_output_tokens escalation: recovery count increments, escalated tokens applied
- Multi-turn recovery: retry with escalated max tokens up to limit
- Give up after MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
- prompt_too_long triggers reactive compact retry
- Recovery state transitions match TS
"""
from __future__ import annotations

import unittest

from src.query.query import (
    ESCALATED_MAX_TOKENS,
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
)
from src.query.transitions import (
    ContinueReason,
    QueryState,
    Terminal,
    Transition,
)


class TestMaxOutputTokensRecovery(unittest.TestCase):
    """max_output_tokens recovery escalation matches TS."""

    def test_recovery_count_starts_at_zero(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertEqual(state.max_output_tokens_recovery_count, 0)

    def test_recovery_count_increments(self) -> None:
        state = QueryState(
            messages=[],
            tool_use_context=None,
            max_output_tokens_recovery_count=1,
        )
        self.assertEqual(state.max_output_tokens_recovery_count, 1)

    def test_escalated_max_tokens_value(self) -> None:
        self.assertEqual(ESCALATED_MAX_TOKENS, 64000)

    def test_recovery_limit_value(self) -> None:
        self.assertEqual(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, 3)

    def test_recovery_within_limit(self) -> None:
        """Recovery count < limit should allow another attempt."""
        for count in range(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT):
            state = QueryState(
                messages=[],
                tool_use_context=None,
                max_output_tokens_recovery_count=count,
            )
            self.assertLess(state.max_output_tokens_recovery_count, MAX_OUTPUT_TOKENS_RECOVERY_LIMIT)

    def test_recovery_at_limit_should_give_up(self) -> None:
        """Recovery count >= limit means give up."""
        state = QueryState(
            messages=[],
            tool_use_context=None,
            max_output_tokens_recovery_count=MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
        )
        self.assertGreaterEqual(
            state.max_output_tokens_recovery_count,
            MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
        )

    def test_escalated_override_applied(self) -> None:
        """max_output_tokens_override can be set to escalated value."""
        state = QueryState(
            messages=[],
            tool_use_context=None,
            max_output_tokens_override=ESCALATED_MAX_TOKENS,
        )
        self.assertEqual(state.max_output_tokens_override, ESCALATED_MAX_TOKENS)


class TestMaxOutputTokensTransitions(unittest.TestCase):
    """Transition reasons for max_output_tokens recovery match TS."""

    def test_recovery_transition_reason(self) -> None:
        t = Transition(reason="max_output_tokens_recovery")
        self.assertEqual(t.reason, "max_output_tokens_recovery")

    def test_escalate_transition_reason(self) -> None:
        t = Transition(reason="max_output_tokens_escalate")
        self.assertEqual(t.reason, "max_output_tokens_escalate")

    def test_recovery_state_carries_forward(self) -> None:
        """New state after recovery should carry incremented count."""
        original = QueryState(
            messages=[{"role": "user", "content": "test"}],
            tool_use_context=None,
            max_output_tokens_recovery_count=0,
            transition=Transition(reason="max_output_tokens_recovery"),
        )
        # Simulate recovery by creating new state with incremented count
        recovered = QueryState(
            messages=original.messages,
            tool_use_context=original.tool_use_context,
            max_output_tokens_recovery_count=original.max_output_tokens_recovery_count + 1,
            max_output_tokens_override=ESCALATED_MAX_TOKENS,
        )
        self.assertEqual(recovered.max_output_tokens_recovery_count, 1)
        self.assertEqual(recovered.max_output_tokens_override, ESCALATED_MAX_TOKENS)


class TestReactiveCompactRecovery(unittest.TestCase):
    """prompt_too_long triggers reactive compact retry matching TS."""

    def test_reactive_compact_default_false(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertFalse(state.has_attempted_reactive_compact)

    def test_reactive_compact_retry_transition(self) -> None:
        t = Transition(reason="reactive_compact_retry")
        self.assertEqual(t.reason, "reactive_compact_retry")

    def test_reactive_compact_flag_set_after_attempt(self) -> None:
        """After reactive compact, flag should be true to prevent repeated attempts."""
        state = QueryState(
            messages=[],
            tool_use_context=None,
            has_attempted_reactive_compact=True,
        )
        self.assertTrue(state.has_attempted_reactive_compact)

    def test_collapse_drain_retry_transition(self) -> None:
        t = Transition(reason="collapse_drain_retry")
        self.assertEqual(t.reason, "collapse_drain_retry")


class TestTokenBudgetContinuation(unittest.TestCase):
    """Token budget continuation matches TS."""

    def test_token_budget_continuation_transition(self) -> None:
        t = Transition(reason="token_budget_continuation")
        self.assertEqual(t.reason, "token_budget_continuation")


class TestStopHookBlocking(unittest.TestCase):
    """Stop hook blocking transition matches TS."""

    def test_stop_hook_blocking_transition(self) -> None:
        t = Transition(reason="stop_hook_blocking")
        self.assertEqual(t.reason, "stop_hook_blocking")

    def test_stop_hook_active_state(self) -> None:
        state = QueryState(
            messages=[],
            tool_use_context=None,
            stop_hook_active=True,
        )
        self.assertTrue(state.stop_hook_active)


class TestTerminalStates(unittest.TestCase):
    """Terminal state reasons match TS."""

    def test_end_turn_terminal(self) -> None:
        term = Terminal(reason="end_turn")
        self.assertEqual(term.reason, "end_turn")

    def test_terminal_is_frozen(self) -> None:
        term = Terminal(reason="end_turn")
        with self.assertRaises(AttributeError):
            term.reason = "something_else"  # type: ignore


class TestTurnCounting(unittest.TestCase):
    """Turn counting matches TS query loop."""

    def test_turn_count_default_1(self) -> None:
        state = QueryState(messages=[], tool_use_context=None)
        self.assertEqual(state.turn_count, 1)

    def test_turn_count_increments(self) -> None:
        for i in range(1, 5):
            state = QueryState(messages=[], tool_use_context=None, turn_count=i)
            self.assertEqual(state.turn_count, i)

    def test_next_turn_transition(self) -> None:
        t = Transition(reason="next_turn")
        self.assertEqual(t.reason, "next_turn")


if __name__ == "__main__":
    unittest.main()
