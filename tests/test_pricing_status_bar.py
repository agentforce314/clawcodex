"""Pricing helpers backing the status-bar cost display.

Covers ``get_pricing`` prefix matching, ``compute_cost`` for the modern
model lineup, ``compute_session_cost`` worker+advisor accumulation, and
``format_cost_usd`` decimal-place selection by magnitude.
"""
from __future__ import annotations

import unittest

from src.services.pricing import (
    DEFAULT_PRICING,
    PRICING,
    compute_cost,
    compute_session_cost,
    format_cost_usd,
    get_pricing,
)


class TestGetPricing(unittest.TestCase):
    def test_exact_match_haiku_4_5(self) -> None:
        p = get_pricing("claude-haiku-4-5")
        self.assertEqual(p["input"], 1.0 / 1_000_000)
        self.assertEqual(p["output"], 5.0 / 1_000_000)

    def test_exact_match_opus_4_7(self) -> None:
        # Opus 4.7 sits on the 5/25 tier (per TS modelCost.ts mirror).
        p = get_pricing("claude-opus-4-7")
        self.assertEqual(p["input"], 5.0 / 1_000_000)
        self.assertEqual(p["output"], 25.0 / 1_000_000)

    def test_exact_match_sonnet_4_6(self) -> None:
        p = get_pricing("claude-sonnet-4-6")
        self.assertEqual(p["input"], 3.0 / 1_000_000)
        self.assertEqual(p["output"], 15.0 / 1_000_000)

    def test_exact_match_minimax_m3(self) -> None:
        # MiniMax-M3 standard tier: $0.60 in / $2.40 out / $0.12 cache read.
        p = get_pricing("MiniMax-M3")
        self.assertEqual(p["input"], 0.6 / 1_000_000)
        self.assertEqual(p["output"], 2.4 / 1_000_000)
        self.assertEqual(p["cache_read"], 0.12 / 1_000_000)

    def test_family_prefix_falls_back_for_future_opus_variant(self) -> None:
        # A model name not in the exact table but matching the
        # ``claude-opus-4-7`` family prefix → 5/25 tier.
        p = get_pricing("claude-opus-4-7-future-experimental")
        self.assertEqual(p["input"], 5.0 / 1_000_000)

    def test_family_prefix_falls_back_for_future_haiku_variant(self) -> None:
        p = get_pricing("claude-haiku-4-9-experimental")
        self.assertEqual(p["input"], 1.0 / 1_000_000)

    def test_openrouter_vendor_prefix_stripped(self) -> None:
        # ``anthropic/claude-opus-4-7`` should price as if the bare
        # model name was passed in.
        p = get_pricing("anthropic/claude-opus-4-7")
        self.assertEqual(p["input"], 5.0 / 1_000_000)
        # The same strip prices DeepSeek-via-OpenRouter at the upstream
        # DeepSeek rate (a directional estimate; the proxy may add markup).
        d = get_pricing("deepseek/deepseek-v4-pro")
        self.assertEqual(d["input"], 0.435 / 1_000_000)

    def test_unknown_model_returns_none(self) -> None:
        # Critic C1: unknowns return None instead of mispricing as
        # Sonnet 3/15 (which would be ~10× off for the cheap non-Claude
        # models the user actually runs). DeepSeek V4 is now tabled, so the
        # still-unpriced examples are Gemini / GPT-tier ids.
        self.assertIsNone(get_pricing("totally-unknown-model-xyz"))
        self.assertIsNone(get_pricing("gpt-5.4"))
        self.assertIsNone(get_pricing("google/gemini-2.5-pro"))

    def test_empty_model_returns_none(self) -> None:
        self.assertIsNone(get_pricing(""))

    def test_legacy_default_pricing_still_exported(self) -> None:
        # ``DEFAULT_PRICING`` is kept for the legacy cost-tracker
        # facade (``services/cost_tracker.py`` falls back to it when
        # get_pricing returns None to preserve the always-charge-
        # something contract). New callers should NOT use it.
        self.assertEqual(DEFAULT_PRICING["input"], 3.0 / 1_000_000)

    def test_no_bare_opus_4_prefix(self) -> None:
        # Critic C2: ``claude-opus-4`` bare prefix was removed so an
        # unknown opus-4.x variant (e.g. ``claude-opus-4-9-future``)
        # falls through to None rather than tagging with the legacy
        # 15/75 tier. Mismatch tier = surprising-large status-bar
        # number = lost user trust.
        self.assertIsNone(get_pricing("claude-opus-4-9-future"))
        # But the known 4.5/4.6/4.7 variants still resolve to 5/25.
        self.assertEqual(
            get_pricing("claude-opus-4-7")["input"], 5.0 / 1_000_000,
        )


class TestComputeCost(unittest.TestCase):
    def test_input_plus_output_no_cache(self) -> None:
        cost = compute_cost("claude-haiku-4-5", {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        })
        # 1M input @ $1 + 1M output @ $5 = $6 exactly
        self.assertAlmostEqual(cost, 6.0, places=6)

    def test_cache_creation_and_read_charged(self) -> None:
        cost = compute_cost("claude-opus-4-7", {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        })
        # opus-4-7 (5/25 tier): $5 input + $6.25 cache_creation + $0.50 cache_read
        self.assertAlmostEqual(cost, 5.0 + 6.25 + 0.50, places=6)

    def test_missing_keys_default_to_zero(self) -> None:
        # No tokens at all → free.
        self.assertEqual(compute_cost("claude-opus-4-7", {}), 0.0)

    def test_minimax_m3_input_output_and_cache(self) -> None:
        # MiniMax-M3: no separate cache-write charge, so cache_creation
        # mirrors input ($0.60/M); cache_read is the low $0.12/M rate.
        cost = compute_cost("MiniMax-M3", {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        })
        # $0.60 input + $2.40 output + $0.60 cache_creation + $0.12 cache_read
        self.assertAlmostEqual(cost, 0.6 + 2.4 + 0.6 + 0.12, places=6)

    def test_none_values_treated_as_zero(self) -> None:
        # Defensive: usage dict might have explicit None from a
        # stale-cache response.
        cost = compute_cost("claude-opus-4-7", {
            "input_tokens": None,
            "output_tokens": 100,
        })
        # 100 output @ opus-4-7's $25/M = $0.0025
        self.assertAlmostEqual(cost, 100 * (25.0 / 1_000_000), places=8)


class TestComputeSessionCost(unittest.TestCase):
    def test_worker_only(self) -> None:
        worker, advisor, total = compute_session_cost(
            worker_model="claude-haiku-4-5",
            worker_input_tokens=10_000,
            worker_output_tokens=5_000,
        )
        # 10k @ $1/M + 5k @ $5/M
        expected = 10_000 * (1.0 / 1_000_000) + 5_000 * (5.0 / 1_000_000)
        self.assertAlmostEqual(worker, expected, places=8)
        self.assertEqual(advisor, 0.0)
        self.assertEqual(total, worker)

    def test_worker_plus_advisor(self) -> None:
        worker, advisor, total = compute_session_cost(
            worker_model="claude-haiku-4-5",
            worker_input_tokens=10_000,
            worker_output_tokens=5_000,
            advisor_model="claude-opus-4-7",
            advisor_input_tokens=2_000,
            advisor_output_tokens=1_000,
        )
        expected_worker = 10_000 * (1.0 / 1_000_000) + 5_000 * (5.0 / 1_000_000)
        expected_advisor = 2_000 * (5.0 / 1_000_000) + 1_000 * (25.0 / 1_000_000)
        self.assertAlmostEqual(worker, expected_worker, places=8)
        self.assertAlmostEqual(advisor, expected_advisor, places=8)
        self.assertAlmostEqual(total, expected_worker + expected_advisor, places=8)

    def test_advisor_unset_skips_advisor_cost(self) -> None:
        # Empty advisor_model + non-zero advisor tokens → zero advisor
        # cost (don't price an unconfigured advisor; the tokens belong
        # to something else).
        _, advisor, _ = compute_session_cost(
            worker_model="claude-haiku-4-5",
            worker_input_tokens=0,
            worker_output_tokens=0,
            advisor_model="",
            advisor_input_tokens=100,
            advisor_output_tokens=50,
        )
        self.assertEqual(advisor, 0.0)

    def test_zero_tokens_short_circuits(self) -> None:
        worker, advisor, total = compute_session_cost(
            worker_model="claude-haiku-4-5",
            worker_input_tokens=0,
            worker_output_tokens=0,
        )
        self.assertEqual(worker, 0.0)
        self.assertEqual(advisor, 0.0)
        self.assertEqual(total, 0.0)


class TestFormatCostUsd(unittest.TestCase):
    def test_zero_renders_padded(self) -> None:
        # Always 4 decimals for zero so callers' string-length
        # assumptions don't break.
        self.assertEqual(format_cost_usd(0.0), "$0.0000")
        self.assertEqual(format_cost_usd(-1.0), "$0.0000")

    def test_sub_cent_uses_4_decimals(self) -> None:
        self.assertEqual(format_cost_usd(0.0034), "$0.0034")
        self.assertEqual(format_cost_usd(0.001234), "$0.0012")

    def test_under_ten_dollars_uses_3_decimals(self) -> None:
        self.assertEqual(format_cost_usd(0.012), "$0.012")
        self.assertEqual(format_cost_usd(1.2345), "$1.234")
        self.assertEqual(format_cost_usd(9.999), "$9.999")

    def test_over_ten_dollars_uses_2_decimals(self) -> None:
        self.assertEqual(format_cost_usd(10.0), "$10.00")
        self.assertEqual(format_cost_usd(12.3456), "$12.35")
        self.assertEqual(format_cost_usd(1234.5), "$1234.50")

    def test_boundary_at_one_cent(self) -> None:
        # ``< 0.01`` cutoff — exactly $0.01 should use 3 decimals
        # (cents-precision range), not 4-decimal sub-cent format.
        self.assertEqual(format_cost_usd(0.01), "$0.010")
        # Just below the cutoff stays at 4 decimals.
        self.assertEqual(format_cost_usd(0.0099), "$0.0099")

    def test_boundary_at_ten_dollars(self) -> None:
        # ``< 10`` cutoff — exactly $10.00 uses penny-rounding,
        # just below stays at 3 decimals.
        self.assertEqual(format_cost_usd(10.0), "$10.00")
        self.assertEqual(format_cost_usd(9.99), "$9.990")

    def test_compute_cost_unknown_model_returns_zero(self) -> None:
        # Critic C1: instead of mispricing, compute_cost returns 0
        # for unknowns — the status bar's "hidden when zero" check
        # naturally suppresses the segment.
        cost = compute_cost("totally-unknown-model-xyz", {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        })
        self.assertEqual(cost, 0.0)


if __name__ == "__main__":
    unittest.main()
