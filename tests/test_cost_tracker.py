"""Tests for cost tracker."""

from __future__ import annotations

from src.services.cost_tracker import CostTracker, _get_pricing, PRICING, DEFAULT_PRICING


class TestGetPricing:
    def test_known_model(self):
        pricing = _get_pricing("claude-sonnet-4-20250514")
        assert pricing == PRICING["claude-sonnet-4-20250514"]

    def test_unknown_model_defaults(self):
        pricing = _get_pricing("some-unknown-model")
        assert pricing == DEFAULT_PRICING

    def test_prefix_matching(self):
        pricing = _get_pricing("claude-3-5-sonnet-20241022-v2")
        assert pricing is not None


class TestCostTracker:
    def test_initial_state(self):
        tracker = CostTracker()
        assert tracker.get_total_cost() == 0.0
        assert tracker.get_turn_cost() == 0.0
        assert tracker.get_total_input_tokens() == 0
        assert tracker.get_total_output_tokens() == 0

    def test_record_usage(self):
        tracker = CostTracker()
        cost = tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        assert cost > 0
        assert tracker.get_total_cost() == cost
        assert tracker.get_total_input_tokens() == 1000
        assert tracker.get_total_output_tokens() == 500

    def test_cumulative_cost(self):
        tracker = CostTracker()
        cost1 = tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 100,
            "output_tokens": 50,
        })
        cost2 = tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 200,
            "output_tokens": 100,
        })
        assert tracker.get_total_cost() == cost1 + cost2
        assert tracker.get_total_input_tokens() == 300
        assert tracker.get_total_output_tokens() == 150

    def test_turn_cost_and_reset(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 100,
            "output_tokens": 50,
        })
        turn1_cost = tracker.get_turn_cost()
        assert turn1_cost > 0

        tracker.reset_turn()
        assert tracker.get_turn_cost() == 0.0
        assert tracker.get_total_cost() == turn1_cost

        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 200,
            "output_tokens": 100,
        })
        assert tracker.get_turn_cost() > 0
        assert tracker.get_total_cost() > turn1_cost

    def test_cache_savings(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 10000,
        })
        savings = tracker.get_cache_savings()
        assert savings > 0

    def test_is_over_budget(self):
        tracker = CostTracker()
        assert not tracker.is_over_budget(None)
        assert not tracker.is_over_budget(1.0)
        tracker.record_usage("claude-opus-4-20250514", {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        })
        assert tracker.is_over_budget(0.01)

    def test_get_summary(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
        })
        summary = tracker.get_summary()
        assert summary["total_cost_usd"] > 0
        assert summary["total_input_tokens"] == 100
        assert summary["total_output_tokens"] == 50
        assert summary["total_cache_creation_tokens"] == 200
        assert summary["total_cache_read_tokens"] == 300
        assert summary["event_count"] == 1

    def test_backward_compat_record(self):
        tracker = CostTracker()
        tracker.record("test", 100)
        assert tracker.get_total_input_tokens() == 100
