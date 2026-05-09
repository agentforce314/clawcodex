"""Phase-4 / WI-4.1 — aggregate_hook_results regression tests.

Covers:
  * Empty input → no opinion.
  * deny > ask > allow precedence in all permutations.
  * Reason attribution (first deny/ask wins; allow uses first allow).
  * contributing_reasons captures every hook's decision in firing order.
  * Non-decision payload aggregation: blocking_error, additional_contexts,
    updated_input, prevent_continuation, updated_mcp_tool_output.
"""

from __future__ import annotations

import pytest

from src.hooks.aggregation import AggregatedHookResult, aggregate_hook_results
from src.hooks.hook_types import HookResult


def _r(
    *,
    behavior: str | None = None,
    reason: str | None = None,
    command: str | None = None,
    blocking_error: str | None = None,
    updated_input: dict | None = None,
    additional_contexts: list[str] | None = None,
    prevent_continuation: bool = False,
    stop_reason: str | None = None,
    updated_mcp_tool_output: object = None,
) -> HookResult:
    return HookResult(
        permission_behavior=behavior,
        hook_permission_decision_reason=reason,
        command=command,
        blocking_error=blocking_error,
        updated_input=updated_input,
        additional_contexts=additional_contexts,
        prevent_continuation=prevent_continuation,
        stop_reason=stop_reason,
        updated_mcp_tool_output=updated_mcp_tool_output,
    )


class TestEmptyAndSingleHook:
    def test_empty_returns_no_opinion(self):
        agg = aggregate_hook_results([])
        assert agg.permission_behavior is None
        assert agg.hook_permission_decision_reason is None
        assert agg.contributing_reasons == []

    def test_single_allow(self):
        agg = aggregate_hook_results([_r(behavior="allow", reason="ok")])
        assert agg.permission_behavior == "allow"
        assert agg.hook_permission_decision_reason == "ok"

    def test_single_deny(self):
        agg = aggregate_hook_results([_r(behavior="deny", reason="bad")])
        assert agg.permission_behavior == "deny"
        assert agg.hook_permission_decision_reason == "bad"

    def test_single_ask(self):
        agg = aggregate_hook_results([_r(behavior="ask", reason="check")])
        assert agg.permission_behavior == "ask"
        assert agg.hook_permission_decision_reason == "check"

    def test_single_no_opinion(self):
        agg = aggregate_hook_results([_r(behavior=None)])
        assert agg.permission_behavior is None


class TestDenyAskAllowPrecedence:
    def test_deny_beats_ask(self):
        agg = aggregate_hook_results([
            _r(behavior="ask", reason="ask first"),
            _r(behavior="deny", reason="deny second"),
        ])
        assert agg.permission_behavior == "deny"
        assert agg.hook_permission_decision_reason == "deny second"

    def test_deny_beats_allow(self):
        agg = aggregate_hook_results([
            _r(behavior="allow", reason="allow first"),
            _r(behavior="deny", reason="deny second"),
        ])
        assert agg.permission_behavior == "deny"
        assert agg.hook_permission_decision_reason == "deny second"

    def test_ask_beats_allow(self):
        agg = aggregate_hook_results([
            _r(behavior="allow", reason="allow first"),
            _r(behavior="ask", reason="ask second"),
        ])
        assert agg.permission_behavior == "ask"
        assert agg.hook_permission_decision_reason == "ask second"

    def test_allow_beats_no_opinion(self):
        agg = aggregate_hook_results([
            _r(behavior=None),
            _r(behavior="allow", reason="ok"),
        ])
        assert agg.permission_behavior == "allow"

    def test_three_way_deny_wins(self):
        # Critic ask: 3 hooks (allow, ask, deny) on same tool → final
        # aggregate is deny with all three reasons attributed.
        agg = aggregate_hook_results([
            _r(behavior="allow", reason="user-allow", command="hook-a"),
            _r(behavior="ask", reason="project-ask", command="hook-b"),
            _r(behavior="deny", reason="policy-deny", command="hook-c"),
        ])
        assert agg.permission_behavior == "deny"
        assert agg.hook_permission_decision_reason == "policy-deny"
        # All three reasons attributed in firing order.
        assert agg.contributing_reasons == [
            ("allow", "user-allow", "hook-a"),
            ("ask", "project-ask", "hook-b"),
            ("deny", "policy-deny", "hook-c"),
        ]

    def test_first_deny_reason_wins_among_multiple_denies(self):
        agg = aggregate_hook_results([
            _r(behavior="deny", reason="first"),
            _r(behavior="deny", reason="second"),
        ])
        assert agg.permission_behavior == "deny"
        assert agg.hook_permission_decision_reason == "first"

    def test_first_ask_reason_wins_among_multiple_asks(self):
        agg = aggregate_hook_results([
            _r(behavior="ask", reason="first"),
            _r(behavior="ask", reason="second"),
        ])
        assert agg.hook_permission_decision_reason == "first"


class TestNonDecisionPayloadMerge:
    def test_first_blocking_error_wins(self):
        agg = aggregate_hook_results([
            _r(blocking_error="boom1"),
            _r(blocking_error="boom2"),
        ])
        assert agg.blocking_error == "boom1"

    def test_additional_contexts_concatenated(self):
        agg = aggregate_hook_results([
            _r(additional_contexts=["a"]),
            _r(additional_contexts=["b", "c"]),
        ])
        assert agg.additional_contexts == ["a", "b", "c"]

    def test_updated_input_last_wins(self):
        agg = aggregate_hook_results([
            _r(updated_input={"k": "first"}),
            _r(updated_input={"k": "last"}),
        ])
        assert agg.updated_input == {"k": "last"}

    def test_prevent_continuation_or_semantics(self):
        agg = aggregate_hook_results([
            _r(prevent_continuation=False),
            _r(prevent_continuation=True, stop_reason="done"),
        ])
        assert agg.prevent_continuation is True
        assert agg.stop_reason == "done"

    def test_updated_mcp_tool_output_last_wins(self):
        agg = aggregate_hook_results([
            _r(updated_mcp_tool_output={"first": 1}),
            _r(updated_mcp_tool_output={"last": 2}),
        ])
        assert agg.updated_mcp_tool_output == {"last": 2}


class TestContributingReasons:
    def test_attribution_in_firing_order(self):
        agg = aggregate_hook_results([
            _r(behavior=None, command="silent"),
            _r(behavior="allow", reason="r1", command="a"),
            _r(behavior=None, command="quiet"),
            _r(behavior="deny", reason="r2", command="d"),
        ])
        assert agg.contributing_reasons == [
            (None, None, "silent"),
            ("allow", "r1", "a"),
            (None, None, "quiet"),
            ("deny", "r2", "d"),
        ]
