"""Tests for token budget."""

from __future__ import annotations

from src.query.token_budget import (
    BudgetTracker,
    ContinueDecision,
    StopDecision,
    check_token_budget,
    create_budget_tracker,
    find_token_budget_positions,
    get_budget_continuation_message,
    parse_token_budget,
)


class TestCheckTokenBudget:
    def test_agent_always_stops(self):
        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, "agent_1", 100000, 0)
        assert isinstance(decision, StopDecision)
        assert decision.action == "stop"

    def test_no_budget_stops(self):
        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, None, None, 0)
        assert isinstance(decision, StopDecision)

    def test_zero_budget_stops(self):
        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, None, 0, 0)
        assert isinstance(decision, StopDecision)

    def test_under_threshold_continues(self):
        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, None, 100000, 50000)
        assert isinstance(decision, ContinueDecision)
        assert decision.action == "continue"
        assert decision.pct == 50
        assert tracker.continuation_count == 1

    def test_over_threshold_stops(self):
        tracker = create_budget_tracker()
        decision = check_token_budget(tracker, None, 100000, 95000)
        assert isinstance(decision, StopDecision)

    def test_diminishing_returns(self):
        tracker = create_budget_tracker()
        tracker.continuation_count = 3
        tracker.last_delta_tokens = 100
        tracker.last_global_turn_tokens = 50000
        decision = check_token_budget(tracker, None, 100000, 50100)
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is not None
        assert decision.completion_event["diminishing_returns"] is True

    def test_continuation_count_increments(self):
        tracker = create_budget_tracker()
        check_token_budget(tracker, None, 100000, 10000)
        assert tracker.continuation_count == 1
        check_token_budget(tracker, None, 100000, 30000)
        assert tracker.continuation_count == 2
        check_token_budget(tracker, None, 100000, 50000)
        assert tracker.continuation_count == 3


class TestGetBudgetContinuationMessage:
    def test_message_format(self):
        msg = get_budget_continuation_message(50, 50000, 100000)
        assert "50%" in msg
        assert "50,000" in msg
        assert "100,000" in msg
        assert "do not summarize" in msg

    def test_unicode_dash(self):
        msg = get_budget_continuation_message(10, 1000, 10000)
        assert "\u2014" in msg


class TestParseTokenBudget:
    def test_shorthand_start(self):
        assert parse_token_budget("+500k do something") == 500_000

    def test_shorthand_end(self):
        assert parse_token_budget("do something +2m") == 2_000_000

    def test_verbose(self):
        assert parse_token_budget("use 1.5m tokens") == 1_500_000

    def test_verbose_spend(self):
        assert parse_token_budget("spend 100k tokens") == 100_000

    def test_case_insensitive(self):
        assert parse_token_budget("+500K") == 500_000
        assert parse_token_budget("use 1M tokens") == 1_000_000

    def test_no_budget(self):
        assert parse_token_budget("just do something") is None

    def test_billions(self):
        assert parse_token_budget("+1b") == 1_000_000_000

    def test_decimal(self):
        assert parse_token_budget("+1.5k") == 1500


class TestFindTokenBudgetPositions:
    def test_shorthand_start(self):
        positions = find_token_budget_positions("+500k do something")
        assert len(positions) >= 1

    def test_verbose(self):
        positions = find_token_budget_positions("use 1m tokens here")
        assert len(positions) >= 1

    def test_no_positions(self):
        positions = find_token_budget_positions("just normal text")
        assert len(positions) == 0
