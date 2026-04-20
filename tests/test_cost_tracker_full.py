"""Tests for R2-WS-9: Extended cost tracker."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.services.cost_tracker import CostTracker, ModelUsageEntry


class TestDurationTracking:
    def test_api_duration(self):
        tracker = CostTracker()
        tracker.record_api_duration(100.0)
        tracker.record_api_duration(200.0)
        assert tracker.get_api_duration_ms() == 300.0

    def test_tool_duration(self):
        tracker = CostTracker()
        tracker.record_tool_duration(50.0)
        assert tracker.get_tool_duration_ms() == 50.0

    def test_session_duration(self):
        tracker = CostTracker()
        # Session duration should be > 0
        dur = tracker.get_total_session_duration_ms()
        assert dur >= 0


class TestLinesChanged:
    def test_record_lines(self):
        tracker = CostTracker()
        tracker.record_lines_changed(added=10, removed=3)
        tracker.record_lines_changed(added=5, removed=2)
        assert tracker.get_lines_added() == 15
        assert tracker.get_lines_removed() == 5


class TestWebSearch:
    def test_web_search_count(self):
        tracker = CostTracker()
        assert tracker.get_web_search_count() == 0
        tracker.record_web_search()
        tracker.record_web_search()
        assert tracker.get_web_search_count() == 2


class TestPerModelAggregation:
    def test_single_model(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 100,
            "output_tokens": 50,
        })
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 200,
            "output_tokens": 100,
        })
        usage = tracker.get_model_usage()
        assert "claude-sonnet-4-20250514" in usage
        entry = usage["claude-sonnet-4-20250514"]
        assert entry.input_tokens == 300
        assert entry.output_tokens == 150
        assert entry.request_count == 2

    def test_multiple_models(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {"input_tokens": 100, "output_tokens": 50})
        tracker.record_usage("claude-3-5-haiku-20241022", {"input_tokens": 200, "output_tokens": 100})
        usage = tracker.get_model_usage()
        assert len(usage) == 2
        assert "claude-sonnet-4-20250514" in usage
        assert "claude-3-5-haiku-20241022" in usage


class TestUnknownModels:
    def test_known_model_not_flagged(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {"input_tokens": 100, "output_tokens": 50})
        assert tracker.has_unknown_models() is False

    def test_unknown_model_flagged(self):
        tracker = CostTracker()
        tracker.record_usage("gpt-4o-unknown", {"input_tokens": 100, "output_tokens": 50})
        assert tracker.has_unknown_models() is True
        assert "gpt-4o-unknown" in tracker.get_unknown_models()


class TestEnhancedSummary:
    def test_summary_includes_new_fields(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {"input_tokens": 100, "output_tokens": 50})
        tracker.record_api_duration(150.0)
        tracker.record_tool_duration(50.0)
        tracker.record_lines_changed(added=10, removed=3)
        tracker.record_web_search()

        summary = tracker.get_summary()
        assert "api_duration_ms" in summary
        assert summary["api_duration_ms"] == 150.0
        assert "tool_duration_ms" in summary
        assert summary["tool_duration_ms"] == 50.0
        assert "session_duration_ms" in summary
        assert summary["session_duration_ms"] >= 0
        assert summary["lines_added"] == 10
        assert summary["lines_removed"] == 3
        assert summary["web_search_count"] == 1
        assert "claude-sonnet-4-20250514" in summary["models_used"]
        assert summary["has_unknown_models"] is False

    def test_cache_savings_in_summary(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 500,
        })
        summary = tracker.get_summary()
        assert summary["cache_savings_usd"] > 0

    def test_backward_compat_fields(self):
        tracker = CostTracker()
        tracker.record_usage("claude-sonnet-4-20250514", {"input_tokens": 100, "output_tokens": 50})
        summary = tracker.get_summary()
        assert "total_cost_usd" in summary
        assert "total_input_tokens" in summary
        assert "total_output_tokens" in summary
        assert "event_count" in summary
