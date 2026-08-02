"""`result.usage` must be a complete BILLING total, not input+output.

``input_tokens`` counts only cache MISSES — that is the shared convention
across providers (Anthropic reports it natively; OpenAI-compatible providers
since the cache-read split). Summing input+output alone therefore dropped
every cached token from the total, and `agent_server` feeds that same dict
straight to `compute_cost`.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.entrypoints.headless import _accumulate_usage, _billed_token_total
from src.query.agent_loop_compat import run_query_as_agent_loop
from src.services.pricing import compute_cost
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage


def _run(coro):
    return asyncio.run(coro)


def _provider(usage: dict) -> MagicMock:
    provider = MagicMock()
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.return_value = ChatResponse(
        content="done",
        model="test",
        usage=usage,
        finish_reason="end_turn",
        tool_uses=None,
    )
    return provider


class TestUsageAggregationCarriesCacheTokens(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _usage_for(self, usage: dict) -> dict:
        result = _run(
            run_query_as_agent_loop(
                initial_messages=[UserMessage(content="Hi")],
                provider=_provider(usage),
                tool_registry=self.registry,
                tool_context=self.context,
                system_prompt="You are helpful.",
                max_turns=5,
            )
        )
        return result.usage

    def test_cache_tokens_survive_the_cumulative_sum(self):
        """Both counters carry through, with distinct non-zero values.

        Both matter and they are priced differently — Anthropic charges a
        premium to WRITE the cache and a discount to READ it. Testing either
        one at zero would not distinguish summing it from dropping it.
        """
        usage = self._usage_for(
            {
                "input_tokens": 53,
                "output_tokens": 8,
                "cache_read_input_tokens": 2560,
                "cache_creation_input_tokens": 384,
            }
        )
        self.assertEqual(usage["input_tokens"], 53)
        self.assertEqual(usage["output_tokens"], 8)
        self.assertEqual(usage["cache_read_input_tokens"], 2560)
        self.assertEqual(usage["cache_creation_input_tokens"], 384)

    def test_the_aggregated_dict_prices_a_cached_turn_correctly(self):
        """`agent_server` passes this dict straight to `compute_cost`.

        Numbers are a real turn measured against DeepSeek: 2613-token prompt,
        2560 of it served from the prefix cache. Without the cache counters
        the cached portion was billed at nothing — 71.7% of the turn's cost
        simply absent from the total.
        """
        per_turn = {
            "input_tokens": 53,
            "output_tokens": 8,
            "cache_read_input_tokens": 2560,
            "cache_creation_input_tokens": 384,
        }
        aggregated = self._usage_for(per_turn)

        model = "openai/gpt-5.6-luna"
        truth = compute_cost(model, per_turn)
        self.assertGreater(truth, 0)
        self.assertAlmostEqual(compute_cost(model, aggregated), truth, places=12)

        # The identity above is the assertion. A ratio against input+output
        # alone would pin a live pricing rate, breaking on a rate edit for a
        # reason that has nothing to do with aggregation.

    def test_cache_tokens_ACCUMULATE_across_loop_turns(self):
        """The counters must ADD across turns, not just survive one.

        Every other fixture here ends after a single API call, so `+=` and a
        plain `=` are indistinguishable — a mutant swapping them passed the
        whole file. Accumulation IS the claim ("result.usage is a billing
        total"), and turn count is the axis that tests it. Distinct values
        per turn so a last-wins assignment cannot coincide with the sum.
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="running a command",
                model="test",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 64,
                },
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "tool_1",
                        "name": "Bash",
                        "input": {"command": "true", "description": "noop"},
                    }
                ],
            ),
            ChatResponse(
                content="done",
                model="test",
                usage={
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 3000,
                    "cache_creation_input_tokens": 128,
                },
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        result = _run(
            run_query_as_agent_loop(
                initial_messages=[UserMessage(content="Hi")],
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                system_prompt="You are helpful.",
                max_turns=5,
            )
        )

        usage = result.usage
        self.assertEqual(usage["input_tokens"], 300)
        self.assertEqual(usage["output_tokens"], 30)
        self.assertEqual(usage["cache_read_input_tokens"], 4000)
        self.assertEqual(usage["cache_creation_input_tokens"], 192)
        # and the snapshot is still the LAST turn, not the sum
        self.assertEqual(usage["last_cache_read_input_tokens"], 3000)

    def test_a_provider_reporting_no_cache_still_gets_zeroed_counters(self):
        """Keys are always present, so consumers need no `.get` defaults."""
        usage = self._usage_for({"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(usage["cache_read_input_tokens"], 0)
        self.assertEqual(usage["cache_creation_input_tokens"], 0)
        self.assertEqual(usage["input_tokens"], 10)

    def test_input_tokens_keeps_its_meaning(self):
        """The change is additive — existing readers must be untouched.

        Redefining `input_tokens` to include the cache would have been a
        stream-json contract change; adding counters alongside it is not.
        """
        usage = self._usage_for(
            {
                "input_tokens": 53,
                "output_tokens": 8,
                "cache_read_input_tokens": 2560,
                "cache_creation_input_tokens": 7,
            }
        )
        self.assertEqual(usage["input_tokens"], 53)

    def test_the_last_snapshot_still_measures_live_context(self):
        """The cumulative sum is billing; `last_*` remains the context view.

        They must not be conflated: the sum double-counts context because
        every turn re-sends the whole conversation.
        """
        usage = self._usage_for(
            {
                "input_tokens": 53,
                "output_tokens": 8,
                "cache_read_input_tokens": 2560,
                "cache_creation_input_tokens": 0,
            }
        )
        self.assertEqual(usage["last_input_tokens"], 53)
        self.assertEqual(usage["last_cache_read_input_tokens"], 2560)


class TestHeadlessUsageTotals(unittest.TestCase):
    """The multi-prompt accumulator in `entrypoints.headless`.

    Calls the real helpers. An earlier version of this class re-implemented
    the loop inline, which passed with BOTH headless fixes reverted — the
    accumulation lived inside a long function, and mirroring it tested the
    mirror. The helpers were extracted so these assertions reach the code
    that actually runs.
    """

    @staticmethod
    def _accumulate(per_prompt: list[dict]) -> dict:
        usage_total: dict[str, int] = {}
        for usage in per_prompt:
            _accumulate_usage(usage_total, usage)
        return usage_total

    def test_cumulative_keys_add_and_last_keys_replace(self):
        totals = self._accumulate(
            [
                {"input_tokens": 10, "cache_read_input_tokens": 100, "last_input_tokens": 10},
                {"input_tokens": 20, "cache_read_input_tokens": 200, "last_input_tokens": 20},
            ]
        )
        self.assertEqual(totals["input_tokens"], 30)
        self.assertEqual(totals["cache_read_input_tokens"], 300)
        # last-wins: summing a snapshot across prompts measures nothing, and
        # the result rides out in the stream-json payload.
        self.assertEqual(totals["last_input_tokens"], 20)

    def test_the_goal_budget_counts_every_billed_token(self):
        """`/goal` spends against this; `input_tokens` alone is cache misses.

        On a warm prefix cache the budget saw a small fraction of what had
        actually been spent.
        """
        totals = self._accumulate(
            [
                {
                    "input_tokens": 53,
                    "output_tokens": 8,
                    "cache_read_input_tokens": 2560,
                    "cache_creation_input_tokens": 0,
                }
            ]
        )
        # 53 miss + 8 out + 2560 cached. The old computation saw 61 of these
        # 2621 — the assertion that matters is the identity, not the ratio.
        self.assertEqual(_billed_token_total(totals), 2621)


if __name__ == "__main__":
    unittest.main()


class TestTurnCostIsNotPricedFromTheAggregate(unittest.TestCase):
    """Cost must be priced per REQUEST, never over the cumulative dict.

    `get_pricing` selects a tier from a per-request threshold
    (`gpt-5.6-luna` at 272K, `MiniMax-M3` at 512K). `result.usage` is the sum
    across every loop turn, and cache reads sum to roughly
    turns x conversation-size — so a long loop of small requests crosses a
    boundary no single request came near.

    This is the trap that makes completing the cumulative dict actively
    dangerous for anything that then prices it: making the token total
    accurate is exactly what pushes the aggregate over the threshold.
    """

    MODEL = "openai/gpt-5.6-luna"
    PER_REQUEST = {
        "input_tokens": 400,
        "output_tokens": 300,
        "cache_read_input_tokens": 15_000,
        "cache_creation_input_tokens": 0,
    }
    TURNS = 25

    def test_pricing_the_aggregate_crosses_a_per_request_tier(self):
        """Documents WHY the server must not call compute_cost on the sum."""
        truth = sum(
            compute_cost(self.MODEL, self.PER_REQUEST) for _ in range(self.TURNS)
        )
        aggregate = {k: v * self.TURNS for k, v in self.PER_REQUEST.items()}
        priced_as_aggregate = compute_cost(self.MODEL, aggregate)

        prompt_total = (
            aggregate["input_tokens"] + aggregate["cache_read_input_tokens"]
        )
        self.assertGreater(prompt_total, 272_000, "fixture must cross the tier")
        self.assertGreater(
            priced_as_aggregate,
            truth * 1.5,
            "pricing the aggregate should over-bill once the tier is crossed",
        )

    def test_below_the_tier_the_two_agree(self):
        """The hazard is the threshold, not aggregation itself."""
        turns = 5
        truth = sum(compute_cost(self.MODEL, self.PER_REQUEST) for _ in range(turns))
        aggregate = {k: v * turns for k, v in self.PER_REQUEST.items()}
        self.assertAlmostEqual(compute_cost(self.MODEL, aggregate), truth, places=12)

    def test_the_running_total_prices_each_request_separately(self):
        """The mechanism the server now reads: a delta of this total.

        `record_api_usage` prices each response as it arrives, so the tier is
        always chosen from one request and the accumulated total equals the
        per-request sum even across a tier-crossing run.
        """
        from src.bootstrap.state import get_total_cost_usd, reset_cost_state
        from src.cost_tracker import record_api_usage

        reset_cost_state()
        try:
            before = get_total_cost_usd()
            for _ in range(self.TURNS):
                record_api_usage(self.MODEL, dict(self.PER_REQUEST))
            delta = get_total_cost_usd() - before

            truth = sum(
                compute_cost(self.MODEL, self.PER_REQUEST) for _ in range(self.TURNS)
            )
            self.assertAlmostEqual(delta, truth, places=10)

            # and it is materially BELOW what pricing the aggregate would give
            aggregate = {k: v * self.TURNS for k, v in self.PER_REQUEST.items()}
            self.assertLess(delta, compute_cost(self.MODEL, aggregate) * 0.7)
        finally:
            reset_cost_state()
