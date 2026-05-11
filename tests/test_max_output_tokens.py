"""Tests for the output-token cap helpers (Phase B)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from src.services.api.claude import (
    CAPPED_DEFAULT_MAX_TOKENS,
    CallModelOptions,
    MAX_NON_STREAMING_TOKENS,
    adjust_params_for_non_streaming,
    get_max_output_tokens_for_model,
)


class TestGetMaxOutputTokensForModel(unittest.TestCase):
    def setUp(self) -> None:
        # Strip the env var so each test starts clean.
        self._saved_env = os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)
        if self._saved_env is not None:
            os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = self._saved_env

    def test_opus_default_capped_at_8k(self) -> None:
        # Native default is 64K but the slot-reservation cap clamps to 8K
        # so production p99 (~5K) requests don't reserve 8-16× slot capacity.
        self.assertEqual(
            get_max_output_tokens_for_model("claude-opus-4-7"),
            CAPPED_DEFAULT_MAX_TOKENS,
        )

    def test_haiku_default_at_native(self) -> None:
        # Haiku's native default is already 8K (matches the cap), so the
        # cap is a no-op rather than a downgrade.
        self.assertEqual(
            get_max_output_tokens_for_model("claude-haiku-4-5"),
            CAPPED_DEFAULT_MAX_TOKENS,
        )

    def test_unknown_model_falls_back_to_default(self) -> None:
        self.assertEqual(
            get_max_output_tokens_for_model("imaginary-model"),
            CAPPED_DEFAULT_MAX_TOKENS,
        )

    def test_env_override_below_upper_limit(self) -> None:
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "12000"
        self.assertEqual(
            get_max_output_tokens_for_model("claude-opus-4-7"),
            12000,
        )

    def test_env_override_above_upper_limit_bounded(self) -> None:
        # Opus's native upper-limit is 64K; an env value of 100K must be
        # bounded to 64K.
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "100000"
        self.assertEqual(
            get_max_output_tokens_for_model("claude-opus-4-7"),
            64_000,
        )

    def test_env_override_invalid_falls_back_to_default(self) -> None:
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "not-a-number"
        self.assertEqual(
            get_max_output_tokens_for_model("claude-opus-4-7"),
            CAPPED_DEFAULT_MAX_TOKENS,
        )

    def test_env_override_zero_falls_back_to_default(self) -> None:
        # A zero env override is meaningless (would 400); fall back.
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "0"
        self.assertEqual(
            get_max_output_tokens_for_model("claude-opus-4-7"),
            CAPPED_DEFAULT_MAX_TOKENS,
        )


class TestAdjustParamsForNonStreaming(unittest.TestCase):
    def test_caps_max_tokens(self) -> None:
        capped, thinking = adjust_params_for_non_streaming(
            128_000, thinking_budget=None,
        )
        self.assertEqual(capped, MAX_NON_STREAMING_TOKENS)
        self.assertIsNone(thinking)

    def test_below_cap_unchanged(self) -> None:
        capped, thinking = adjust_params_for_non_streaming(
            8_000, thinking_budget=4_000,
        )
        self.assertEqual(capped, 8_000)
        self.assertEqual(thinking, 4_000)

    def test_thinking_budget_shrinks_when_at_or_above_max(self) -> None:
        # API requires max_tokens > thinking.budget_tokens. When both are at
        # the ceiling, thinking shrinks to max - 1.
        capped, thinking = adjust_params_for_non_streaming(
            MAX_NON_STREAMING_TOKENS, thinking_budget=MAX_NON_STREAMING_TOKENS,
        )
        self.assertEqual(capped, MAX_NON_STREAMING_TOKENS)
        self.assertEqual(thinking, MAX_NON_STREAMING_TOKENS - 1)

    def test_thinking_budget_equal_to_max_still_shrinks(self) -> None:
        capped, thinking = adjust_params_for_non_streaming(
            5_000, thinking_budget=5_000,
        )
        self.assertEqual(capped, 5_000)
        self.assertEqual(thinking, 4_999)

    def test_thinking_budget_floor_is_one(self) -> None:
        # When the capped max collapses to a single token, the budget
        # cannot be negative; it floors at 1.
        capped, thinking = adjust_params_for_non_streaming(
            1, thinking_budget=5,
        )
        self.assertEqual(capped, 1)
        self.assertEqual(thinking, 1)


class TestCallModelOptionsResolvedMaxTokens(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)
        if self._saved_env is not None:
            os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = self._saved_env

    def test_default_resolves_to_8k(self) -> None:
        opts = CallModelOptions(model="claude-opus-4-7")
        self.assertEqual(opts.resolved_max_tokens(), CAPPED_DEFAULT_MAX_TOKENS)

    def test_explicit_max_tokens_honoured(self) -> None:
        opts = CallModelOptions(model="claude-opus-4-7", max_tokens=24_000)
        self.assertEqual(opts.resolved_max_tokens(), 24_000)

    def test_explicit_max_tokens_bounded(self) -> None:
        opts = CallModelOptions(model="claude-opus-4-7", max_tokens=200_000)
        self.assertEqual(opts.resolved_max_tokens(), MAX_NON_STREAMING_TOKENS)


if __name__ == "__main__":
    unittest.main()
