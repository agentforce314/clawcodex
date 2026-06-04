"""Tests for token estimation memoization (ch17 round-2).

The cache layer in ``src/token_estimation.py`` lives at two levels:

  - ``count_tokens(text)`` is wrapped by ``_TEXT_CACHE`` (LRU 4096),
    keyed on ``hash(text)``.
  - ``rough_token_count_estimation_for_block(block)`` is wrapped by
    ``_BLOCK_CACHE`` (LRU 4096), keyed on a stable JSON-projection
    hash of the block.

These tests verify:
  - Hit/miss counters via ``get_token_cache_stats()``.
  - LRU bounded-memory behaviour.
  - Hash-determinism for structurally identical dict blocks.
  - End-to-end memoization across ``count_messages_tokens``.
  - Structural performance assertion: two passes over the same
    message list invoke tiktoken's encoder exactly N unique-string
    times, not 2 * N.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src import token_estimation
from src.token_estimation import (
    _MAX_TEXT_CACHE,
    count_messages_tokens,
    count_tokens,
    get_token_cache_stats,
    reset_token_cache,
    rough_token_count_estimation_for_block,
)


@pytest.fixture(autouse=True)
def _reset_cache_fixture():
    """Ensure every test starts and ends with empty caches and zero counters."""
    reset_token_cache()
    yield
    reset_token_cache()


# ---------------------------------------------------------------------------
# Text-level cache (count_tokens)
# ---------------------------------------------------------------------------


class TestTextCache:
    def test_first_call_is_miss(self):
        count_tokens("hello world")
        stats = get_token_cache_stats()
        assert stats["text_cache_misses"] == 1
        assert stats["text_cache_hits"] == 0

    def test_second_call_is_hit(self):
        count_tokens("hello world")
        count_tokens("hello world")
        stats = get_token_cache_stats()
        assert stats["text_cache_misses"] == 1
        assert stats["text_cache_hits"] == 1

    def test_repeated_same_text_only_misses_once(self):
        for _ in range(50):
            count_tokens("repeated text")
        stats = get_token_cache_stats()
        assert stats["text_cache_misses"] == 1
        assert stats["text_cache_hits"] == 49

    def test_different_strings_are_misses(self):
        count_tokens("first")
        count_tokens("second")
        count_tokens("third")
        stats = get_token_cache_stats()
        assert stats["text_cache_misses"] == 3
        assert stats["text_cache_hits"] == 0

    def test_empty_string_bypasses_cache(self):
        count_tokens("")
        stats = get_token_cache_stats()
        # Empty string short-circuits before the cache layer.
        assert stats["text_cache_misses"] == 0
        assert stats["text_cache_hits"] == 0

    def test_cached_value_matches_uncached_value(self):
        first = count_tokens("the quick brown fox")
        second = count_tokens("the quick brown fox")
        assert first == second

    def test_cache_size_grows_with_unique_inputs(self):
        for i in range(10):
            count_tokens(f"unique-string-{i}")
        stats = get_token_cache_stats()
        assert stats["text_cache_size"] == 10


# ---------------------------------------------------------------------------
# Block-level cache (rough_token_count_estimation_for_block)
# ---------------------------------------------------------------------------


class TestBlockCache:
    def test_identical_text_blocks_hit(self):
        block = {"type": "text", "text": "hello world"}
        rough_token_count_estimation_for_block(block)
        rough_token_count_estimation_for_block(block)
        stats = get_token_cache_stats()
        assert stats["block_cache_misses"] == 1
        assert stats["block_cache_hits"] == 1

    def test_structurally_identical_dicts_hit(self):
        # Two separate dict instances with the same content.
        block_a = {"type": "text", "text": "shared"}
        block_b = {"type": "text", "text": "shared"}
        assert block_a is not block_b
        rough_token_count_estimation_for_block(block_a)
        rough_token_count_estimation_for_block(block_b)
        stats = get_token_cache_stats()
        assert stats["block_cache_misses"] == 1
        assert stats["block_cache_hits"] == 1

    def test_different_block_types_miss(self):
        rough_token_count_estimation_for_block({"type": "text", "text": "hi"})
        rough_token_count_estimation_for_block({"type": "image", "source": {"data": "x"}})
        rough_token_count_estimation_for_block({"type": "tool_use", "name": "Bash", "input": {}})
        stats = get_token_cache_stats()
        assert stats["block_cache_misses"] == 3
        assert stats["block_cache_hits"] == 0

    def test_string_block_caches_via_block_path(self):
        rough_token_count_estimation_for_block("repeated")
        rough_token_count_estimation_for_block("repeated")
        stats = get_token_cache_stats()
        assert stats["block_cache_misses"] == 1
        assert stats["block_cache_hits"] == 1

    def test_tool_use_block_with_dict_input_caches(self):
        block = {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
        rough_token_count_estimation_for_block(block)
        rough_token_count_estimation_for_block(block)
        stats = get_token_cache_stats()
        assert stats["block_cache_hits"] == 1

    def test_unserializable_block_does_not_crash(self):
        # An object that fails JSON serialization should fall through to
        # the impl path without caching, and without raising.
        class _NotSerializable:
            pass

        # Wrap it in a dict so the block path is exercised.
        block = {"type": "unknown", "weird": _NotSerializable()}
        # Should not raise; result may be any non-negative int.
        result = rough_token_count_estimation_for_block(block)
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# Aggregation through count_messages_tokens
# ---------------------------------------------------------------------------


def _make_messages(n: int) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": f"message number {i}"}
        for i in range(n)
    ]


class TestMessagesAggregation:
    def test_second_count_messages_tokens_call_has_zero_new_misses(self):
        messages = _make_messages(20)
        count_messages_tokens(messages)
        stats_after_first = get_token_cache_stats()
        misses_after_first = stats_after_first["text_cache_misses"]

        count_messages_tokens(messages)
        stats_after_second = get_token_cache_stats()
        # No new misses on the second pass — every text was cached.
        assert stats_after_second["text_cache_misses"] == misses_after_first

    def test_grown_list_only_new_messages_miss(self):
        messages = _make_messages(10)
        count_messages_tokens(messages)
        misses_after_first = get_token_cache_stats()["text_cache_misses"]

        # Add 3 new messages, re-run.
        messages.extend(_make_messages(13)[10:])
        count_messages_tokens(messages)
        stats = get_token_cache_stats()
        # Exactly 3 new texts → 3 new misses. (The "role" string "user"
        # only misses once across all calls.)
        new_misses = stats["text_cache_misses"] - misses_after_first
        assert new_misses == 3

    def test_complex_message_with_list_content_cached_via_blocks(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Block one"},
                    {"type": "text", "text": "Block two"},
                ],
            }
        ]
        first = count_messages_tokens(messages)
        second = count_messages_tokens(messages)
        assert first == second
        # The text strings inside the blocks should be cached.
        stats = get_token_cache_stats()
        assert stats["text_cache_hits"] > 0


# ---------------------------------------------------------------------------
# Bounded-memory / LRU eviction
# ---------------------------------------------------------------------------


class TestLRUEviction:
    def test_text_cache_evicts_oldest_when_full(self):
        for i in range(_MAX_TEXT_CACHE + 10):
            count_tokens(f"unique-text-{i}")
        stats = get_token_cache_stats()
        # Capped at max size; never grows beyond it.
        assert stats["text_cache_size"] == _MAX_TEXT_CACHE

    def test_evicted_entry_misses_again(self):
        # Fill the cache.
        for i in range(_MAX_TEXT_CACHE):
            count_tokens(f"slot-{i}")
        misses_after_fill = get_token_cache_stats()["text_cache_misses"]
        # Add one more — evicts "slot-0".
        count_tokens("brand-new")
        # Now access "slot-0" again — must miss.
        count_tokens("slot-0")
        stats = get_token_cache_stats()
        # Two new misses: "brand-new" + re-access of evicted "slot-0".
        assert stats["text_cache_misses"] == misses_after_fill + 2

    def test_recently_accessed_survives_eviction(self):
        for i in range(_MAX_TEXT_CACHE):
            count_tokens(f"slot-{i}")
        misses_after_fill = get_token_cache_stats()["text_cache_misses"]
        # Re-access "slot-0" — moves it to MRU.
        count_tokens("slot-0")
        # Insert one new entry — should evict "slot-1" (now LRU), not slot-0.
        count_tokens("displacer")
        # Re-access slot-0 again — must still hit.
        count_tokens("slot-0")
        stats = get_token_cache_stats()
        # Misses incurred since the fill: just "displacer" = 1.
        assert stats["text_cache_misses"] == misses_after_fill + 1


# ---------------------------------------------------------------------------
# Stats / reset
# ---------------------------------------------------------------------------


class TestStats:
    def test_initial_stats_zero(self):
        stats = get_token_cache_stats()
        assert stats["text_cache_hits"] == 0
        assert stats["text_cache_misses"] == 0
        assert stats["block_cache_hits"] == 0
        assert stats["block_cache_misses"] == 0
        assert stats["text_cache_size"] == 0
        assert stats["block_cache_size"] == 0

    def test_reset_clears_everything(self):
        count_tokens("warmup")
        rough_token_count_estimation_for_block({"type": "text", "text": "warmup"})
        reset_token_cache()
        stats = get_token_cache_stats()
        assert stats == {
            "text_cache_hits": 0,
            "text_cache_misses": 0,
            "block_cache_hits": 0,
            "block_cache_misses": 0,
            "text_cache_size": 0,
            "block_cache_size": 0,
        }


# ---------------------------------------------------------------------------
# Structural performance assertion (encoder call count, not wall-clock)
# ---------------------------------------------------------------------------


class TestEncoderCallCount:
    """Verify that the cache prevents redundant tiktoken encoding.

    We patch the module-level ``_get_encoder`` to return a counting
    proxy. After two ``count_messages_tokens`` passes over the same
    list, the encode call count must equal the count after a single
    pass — no redundant work on the second go.
    """

    def test_two_passes_invoke_encoder_only_for_unique_strings(self):
        real_encoder = token_estimation._get_encoder()
        call_count = {"n": 0}

        class _CountingEncoder:
            def encode(self, text: str):
                call_count["n"] += 1
                if real_encoder is not None:
                    return real_encoder.encode(text)
                return list(text.encode("utf-8"))[: max(1, len(text) // 4)]

        proxy = _CountingEncoder()
        with patch.object(token_estimation, "_get_encoder", return_value=proxy):
            messages = _make_messages(15)
            count_messages_tokens(messages)
            calls_after_first = call_count["n"]
            assert calls_after_first > 0  # sanity: encoder was used

            count_messages_tokens(messages)
            calls_after_second = call_count["n"]

        # Second pass added zero encoder calls — every string was cached.
        assert calls_after_second == calls_after_first


# ---------------------------------------------------------------------------
# Determinism: cache key stability
# ---------------------------------------------------------------------------


class TestCacheKeyDeterminism:
    def test_dict_key_order_matters_for_hash(self):
        """Python 3.7+ preserves dict insertion order; same insertion
        order → same JSON projection → same cache key.

        Two dicts with different insertion order produce different
        JSON. The test verifies the cache still returns correct
        results (just two cache slots instead of one). The token
        count value is identical regardless.
        """
        block_a = {"type": "text", "text": "hi"}
        block_b = {"text": "hi", "type": "text"}
        count_a = rough_token_count_estimation_for_block(block_a)
        count_b = rough_token_count_estimation_for_block(block_b)
        # Values agree even though they live in separate cache slots.
        assert count_a == count_b
