"""
Tests for PR 3: Compaction Telemetry - Cache-hostile compaction detection.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from src.bootstrap.state import (
    CompactionTelemetryData,
    set_compaction_telemetry_data,
    get_compaction_telemetry_data,
    update_compaction_telemetry,
    consume_post_compaction,
)
from src.services.compact.compact import (
    CompactionTelemetry,
    _calculate_cache_hit_rate_from_usage,
    _estimate_compaction_cost_delta,
    log_post_compaction_telemetry,
)


class TestCompactionTelemetryData:
    """Test CompactionTelemetryData dataclass."""

    def test_default_values(self):
        data = CompactionTelemetryData()
        assert data.trigger == "manual"
        assert data.tokens_shed == 0
        assert data.pre_compact_token_count == 0
        assert data.post_compact_token_count == 0
        assert data.compaction_cost_usd == 0.0
        assert data.cache_hit_rate_before is None
        assert data.cache_hit_rate_after is None
        assert data.estimated_cost_delta_usd is None
        assert data.cost_increased is False
        assert data.model is None

    def test_custom_values(self):
        data = CompactionTelemetryData(
            trigger="auto",
            tokens_shed=1000,
            pre_compact_token_count=5000,
            post_compact_token_count=1000,
            compaction_cost_usd=0.001,
            cache_hit_rate_before=80.0,
            cache_hit_rate_after=60.0,
            estimated_cost_delta_usd=0.002,
            cost_increased=True,
            model="test-model",
        )
        assert data.trigger == "auto"
        assert data.tokens_shed == 1000
        assert data.cache_hit_rate_before == 80.0
        assert data.cache_hit_rate_after == 60.0
        assert data.estimated_cost_delta_usd == 0.002
        assert data.cost_increased is True
        assert data.model == "test-model"


class TestCompactionTelemetryState:
    """Test compaction telemetry state management."""

    def setup_method(self):
        # Clear state before each test
        set_compaction_telemetry_data(None)

    def test_set_and_get_telemetry(self):
        data = CompactionTelemetryData(
            trigger="auto",
            tokens_shed=500,
            pre_compact_token_count=3000,
            post_compact_token_count=800,
        )
        set_compaction_telemetry_data(data)
        retrieved = get_compaction_telemetry_data()
        assert retrieved is not None
        assert retrieved.trigger == "auto"
        assert retrieved.tokens_shed == 500

    def test_update_telemetry(self):
        data = CompactionTelemetryData(
            trigger="auto",
            tokens_shed=500,
            pre_compact_token_count=3000,
            post_compact_token_count=800,
            cache_hit_rate_before=80.0,
        )
        set_compaction_telemetry_data(data)

        update_compaction_telemetry(
            cache_hit_rate_after=60.0,
            estimated_cost_delta_usd=0.0015,
            cost_increased=True,
        )

        retrieved = get_compaction_telemetry_data()
        assert retrieved.cache_hit_rate_after == 60.0
        assert retrieved.estimated_cost_delta_usd == 0.0015
        assert retrieved.cost_increased is True

    def test_update_telemetry_partial(self):
        data = CompactionTelemetryData(
            trigger="auto",
            tokens_shed=500,
            pre_compact_token_count=3000,
            post_compact_token_count=800,
            cache_hit_rate_before=80.0,
        )
        set_compaction_telemetry_data(data)

        # Only update cache_hit_rate_after
        update_compaction_telemetry(cache_hit_rate_after=70.0)

        retrieved = get_compaction_telemetry_data()
        assert retrieved.cache_hit_rate_after == 70.0
        assert retrieved.estimated_cost_delta_usd is None
        assert retrieved.cost_increased is False

    def test_update_telemetry_none_state(self):
        # Should not raise when no telemetry data exists
        update_compaction_telemetry(cache_hit_rate_after=60.0)
        assert get_compaction_telemetry_data() is None


class TestCalculateCacheHitRate:
    """Test cache hit rate calculation from usage dict."""

    def test_anthropic_format(self):
        usage = {
            "input_tokens": 1000,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 3500,
        }
        rate = _calculate_cache_hit_rate_from_usage(usage)
        # cache_read / (input + cache_creation + cache_read) = 3500 / 5000 = 70%
        assert rate == 70.0

    def test_openai_format(self):
        usage = {
            "prompt_tokens": 5000,
            "prompt_tokens_details": {"cached_tokens": 3500},
        }
        rate = _calculate_cache_hit_rate_from_usage(usage)
        # cached_tokens / prompt_tokens = 3500 / 5000 = 70%
        assert rate == 70.0

    def test_openai_format_no_cached(self):
        usage = {
            "prompt_tokens": 5000,
            "prompt_tokens_details": {},
        }
        rate = _calculate_cache_hit_rate_from_usage(usage)
        assert rate == 0.0

    def test_no_usage(self):
        usage = {}
        rate = _calculate_cache_hit_rate_from_usage(usage)
        assert rate is None

    def test_zero_tokens(self):
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        rate = _calculate_cache_hit_rate_from_usage(usage)
        assert rate is None


class TestEstimateCompactionCostDelta:
    """Test compaction cost delta estimation."""

    def test_positive_delta_cache_hostile(self):
        # High cache hit rate before, compaction sheds tokens but destroys cache
        delta = _estimate_compaction_cost_delta(
            pre_compact_tokens=5000,
            post_compact_tokens=1000,
            cache_hit_rate_before=90.0,
            cache_hit_rate_after=10.0,
            model="claude-sonnet-4-6",
        )
        # Should return a positive cost (cache-hostile)
        assert delta is not None
        assert delta > 0

    def test_negative_delta_cache_friendly(self):
        # Low cache hit rate before, compaction reduces tokens
        delta = _estimate_compaction_cost_delta(
            pre_compact_tokens=5000,
            post_compact_tokens=1000,
            cache_hit_rate_before=10.0,
            cache_hit_rate_after=None,
            model="claude-sonnet-4-6",
        )
        # Should return some cost estimate
        assert delta is not None

    def test_no_pricing(self):
        delta = _estimate_compaction_cost_delta(
            pre_compact_tokens=5000,
            post_compact_tokens=1000,
            cache_hit_rate_before=50.0,
            cache_hit_rate_after=None,
            model="unknown-model-that-does-not-exist",
        )
        # Should handle missing pricing gracefully
        assert delta is None or delta >= 0


class TestLogPostCompactionTelemetry:
    """Test post-compaction telemetry logging."""

    def setup_method(self):
        set_compaction_telemetry_data(None)

    def test_log_post_compaction_updates_state(self):
        # Set initial telemetry
        set_compaction_telemetry_data(CompactionTelemetryData(
            trigger="auto",
            tokens_shed=1000,
            pre_compact_token_count=5000,
            post_compact_token_count=1000,
            compaction_cost_usd=0.001,
            cache_hit_rate_before=80.0,
            model="claude-sonnet-4-6",
        ))

        # Simulate post-compaction response usage (Anthropic format)
        response_usage = {
            "input_tokens": 2000,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 500,
        }

        # Should not raise
        log_post_compaction_telemetry(
            trigger="auto",
            tokens_shed=1000,
            pre_compact_token_count=5000,
            post_compact_token_count=1000,
            compaction_cost_usd=0.001,
            cache_hit_rate_before=80.0,
            response_usage=response_usage,
            model="claude-sonnet-4-6",
        )

        # Check that state was updated
        telemetry = get_compaction_telemetry_data()
        assert telemetry is not None
        assert telemetry.cache_hit_rate_after is not None
        assert telemetry.estimated_cost_delta_usd is not None
        assert telemetry.cost_increased is not None

    def test_log_post_compaction_openai_format(self):
        set_compaction_telemetry_data(CompactionTelemetryData(
            trigger="manual",
            tokens_shed=500,
            pre_compact_token_count=3000,
            post_compact_token_count=800,
            compaction_cost_usd=0.0005,
            cache_hit_rate_before=50.0,
            model="claude-sonnet-4-6",
        ))

        # OpenAI format usage
        response_usage = {
            "prompt_tokens": 3000,
            "prompt_tokens_details": {"cached_tokens": 1500},
        }

        log_post_compaction_telemetry(
            trigger="manual",
            tokens_shed=500,
            pre_compact_token_count=3000,
            post_compact_token_count=800,
            compaction_cost_usd=0.0005,
            cache_hit_rate_before=50.0,
            response_usage=response_usage,
            model="test-model",
        )

        telemetry = get_compaction_telemetry_data()
        assert telemetry.cache_hit_rate_after == 50.0  # 1500/3000 = 50%


class TestConsumePostCompaction:
    """Test consume_post_compaction flag."""

    def setup_method(self):
        from src.bootstrap.state import mark_post_compaction
        mark_post_compaction()

    def test_consume_returns_true_once(self):
        assert consume_post_compaction() is True
        assert consume_post_compaction() is False
        assert consume_post_compaction() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
