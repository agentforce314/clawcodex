"""Tests for the Phase 2.3 cost-tracker consolidation.

Verifies:
* ``CostTracker.record(label, units)`` still works (legacy back-compat).
* ``CostTracker.record_usage(model, usage)`` routes to bootstrap; two
  instances see the same ``total_cost_usd``.
* ``compute_cost`` returns expected values from pricing tables.
* ``restore_cost_state_for_session`` round-trips a persisted snapshot.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src.bootstrap.state import (
    SessionId,
    get_model_usage,
    get_total_cost_usd,
    reset_state_for_tests,
    switch_session,
)
from src.cost_tracker import CostTracker
from src.services import cost_restore as cost_restore_mod
from src.services.cost_restore import restore_cost_state_for_session
from src.services.pricing import (
    DEFAULT_PRICING,
    PRICING,
    compute_cost,
    get_pricing,
)


@pytest.fixture(autouse=True)
def _reset_bootstrap():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


class TestLegacyRecordApi(unittest.TestCase):
    """The ``record(label, units)`` API stays untouched for back-compat."""

    def test_record_accumulates_total_units(self) -> None:
        t = CostTracker()
        t.record("a", 10)
        t.record("b", 5)
        self.assertEqual(t.total_units, 15)
        self.assertEqual(t.events, ["a:10", "b:5"])

    def test_record_does_not_touch_bootstrap_cost(self) -> None:
        """Legacy units are NOT a USD measurement; they don't flow to
        bootstrap ``total_cost_usd``."""
        t = CostTracker()
        t.record("a", 100)
        self.assertEqual(get_total_cost_usd(), 0.0)


class TestRecordUsage(unittest.TestCase):
    def test_record_usage_returns_cost(self) -> None:
        t = CostTracker()
        cost = t.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 1_000_000, "output_tokens": 0},
        )
        # Sonnet input is $3/M
        self.assertAlmostEqual(cost, 3.0, places=4)

    def test_record_usage_updates_bootstrap_total(self) -> None:
        t = CostTracker()
        t.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 1_000_000},
        )
        self.assertAlmostEqual(get_total_cost_usd(), 3.0, places=4)

    def test_two_trackers_share_bootstrap_total(self) -> None:
        """Architectural invariant: every tracker instance reads the
        same total. No more 'two trackers disagree' bug."""
        a = CostTracker()
        b = CostTracker()
        a.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 500_000},
        )
        b.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 500_000},
        )
        # Both trackers see the same total: $1.50 + $1.50 = $3.00
        self.assertAlmostEqual(a.total_cost_usd, 3.0, places=4)
        self.assertAlmostEqual(b.total_cost_usd, 3.0, places=4)
        self.assertEqual(a.total_cost_usd, b.total_cost_usd)

    def test_record_usage_accumulates_per_model(self) -> None:
        t = CostTracker()
        t.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 1_000_000},
        )
        t.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 500_000},
        )
        usage = get_model_usage()["claude-sonnet-4-20250514"]
        self.assertEqual(usage.input_tokens, 1_500_000)
        self.assertAlmostEqual(usage.cost_usd, 4.5, places=4)

    def test_record_usage_sets_last_usage(self) -> None:
        t = CostTracker()
        t.record_usage(
            "claude-sonnet-4-20250514",
            {"input_tokens": 100, "output_tokens": 50},
        )
        self.assertEqual(t.last_usage, {"input_tokens": 100, "output_tokens": 50})


class TestPricing(unittest.TestCase):
    def test_get_pricing_known_model(self) -> None:
        p = get_pricing("claude-opus-4-20250514")
        self.assertAlmostEqual(p["input"], 15.0 / 1_000_000)
        self.assertAlmostEqual(p["output"], 75.0 / 1_000_000)

    def test_get_pricing_falls_back_to_default(self) -> None:
        p = get_pricing("some-future-model-not-in-table")
        self.assertEqual(p, DEFAULT_PRICING)

    def test_compute_cost_basic(self) -> None:
        cost = compute_cost(
            "claude-sonnet-4-20250514",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            },
        )
        # $3 input + $15 output = $18
        self.assertAlmostEqual(cost, 18.0, places=4)

    def test_compute_cost_with_cache(self) -> None:
        cost = compute_cost(
            "claude-sonnet-4-20250514",
            {
                "cache_creation_input_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
            },
        )
        # $3.75 cache_creation + $0.30 cache_read = $4.05
        self.assertAlmostEqual(cost, 4.05, places=4)

    def test_compute_cost_pure(self) -> None:
        """Computing cost must not mutate bootstrap state."""
        before = get_total_cost_usd()
        compute_cost(
            "claude-sonnet-4-20250514",
            {"input_tokens": 100_000_000},
        )
        self.assertEqual(get_total_cost_usd(), before)


class TestRestoreCostStateForSession:
    """Pytest-style class so we can use ``tmp_path`` to avoid polluting
    the real ``~/.clawcodex/sessions/`` directory."""

    test_sid = SessionId("99999999-9999-9999-9999-999999999999")

    @pytest.fixture(autouse=True)
    def _redirect_sessions_dir(self, tmp_path, monkeypatch):
        """Point ``cost_restore._sessions_dir`` at the test tmp dir."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cost_restore_mod, "_sessions_dir", lambda: sessions_dir)
        self._sessions_dir = sessions_dir
        self._test_file = sessions_dir / f"{self.test_sid}.json"
        yield

    def test_restore_returns_false_when_file_missing(self) -> None:
        result = restore_cost_state_for_session(self.test_sid)
        assert result is False

    def test_restore_returns_false_on_malformed_json(self) -> None:
        self._test_file.write_text("not-json{")
        result = restore_cost_state_for_session(self.test_sid)
        assert result is False

    def test_restore_returns_false_when_persisted_sid_does_not_match(self) -> None:
        """Gate: the persisted file's session_id field must match the
        target. Defends against a renamed/hand-edited file."""
        wrong_snapshot = {
            "session_id": "different-session-id",
            "cost": {"total_cost_usd": 1.0},
        }
        self._test_file.write_text(json.dumps(wrong_snapshot))
        result = restore_cost_state_for_session(self.test_sid)
        assert result is False

    def test_restore_works_without_calling_switch_session_first(self) -> None:
        """Critical TS-parity property: restore should NOT require the
        bootstrap session_id to match the target. The gate is the
        persisted file's session_id, not the runtime one."""
        # Note: we deliberately do NOT call switch_session(test_sid) here
        snapshot = {
            "session_id": str(self.test_sid),
            "cost": {"total_cost_usd": 2.50},
        }
        self._test_file.write_text(json.dumps(snapshot))

        result = restore_cost_state_for_session(self.test_sid)
        assert result is True
        assert abs(get_total_cost_usd() - 2.50) < 1e-6

    def test_restore_applies_persisted_snapshot(self) -> None:
        snapshot = {
            "session_id": str(self.test_sid),
            "cost": {
                "total_cost_usd": 4.20,
                "total_api_duration": 1234,
                "total_api_duration_without_retries": 1100,
                "total_tool_duration": 567,
                "total_lines_added": 100,
                "total_lines_removed": 50,
                "model_usage": {
                    "claude-opus-4-20250514": {
                        "input_tokens": 500_000,
                        "output_tokens": 250_000,
                        "cost_usd": 4.20,
                    },
                },
            },
        }
        self._test_file.write_text(json.dumps(snapshot))

        result = restore_cost_state_for_session(self.test_sid)

        assert result is True
        assert abs(get_total_cost_usd() - 4.20) < 1e-6
        usage = get_model_usage()["claude-opus-4-20250514"]
        assert usage.input_tokens == 500_000
        assert abs(usage.cost_usd - 4.20) < 1e-6


if __name__ == "__main__":
    unittest.main()
