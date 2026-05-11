"""Tests for ``add_cache_breakpoints`` (Phase E).

Tests cover the marker-placement invariant from chapter §"Three Tiers":
*exactly one* cache_control marker per request, on the tail of the
conversation. The skip_cache_write flag shifts the marker one position
earlier so subagent forks don't write a tail that they'll discard.
"""
from __future__ import annotations

import copy
import unittest

from src.services.api.claude import add_cache_breakpoints


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _user_blocks(*blocks: dict) -> dict:
    return {"role": "user", "content": list(blocks)}


class TestAddCacheBreakpointsBasic(unittest.TestCase):
    def test_caching_disabled_returns_input_unchanged(self) -> None:
        msgs = [_user("hi"), _assistant("hello")]
        out = add_cache_breakpoints(msgs, enable_prompt_caching=False)
        self.assertEqual(out, msgs)

    def test_empty_messages_returns_empty(self) -> None:
        out = add_cache_breakpoints([], enable_prompt_caching=True)
        self.assertEqual(out, [])

    def test_single_message_gets_marker(self) -> None:
        msgs = [_user("hello")]
        out = add_cache_breakpoints(msgs)
        self.assertEqual(len(out), 1)
        content = out[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["text"], "hello")
        self.assertIn("cache_control", content[0])
        self.assertEqual(content[0]["cache_control"], {"type": "ephemeral"})


class TestMarkerOnlyOnLast(unittest.TestCase):
    def test_marker_only_on_last_message(self) -> None:
        msgs = [_user("a"), _assistant("b"), _user("c")]
        out = add_cache_breakpoints(msgs)
        self.assertEqual(len(out), 3)

        # First two unchanged.
        self.assertEqual(out[0], _user("a"))
        self.assertEqual(out[1], _assistant("b"))

        # Last has a cache marker on its sole content block.
        last_content = out[2]["content"]
        self.assertIsInstance(last_content, list)
        self.assertIn("cache_control", last_content[-1])

    def test_marker_on_last_block_of_block_list(self) -> None:
        msgs = [
            _user_blocks(
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ),
        ]
        out = add_cache_breakpoints(msgs)
        content = out[0]["content"]
        self.assertEqual(len(content), 2)
        # First block has no marker.
        self.assertNotIn("cache_control", content[0])
        # Last block has the marker.
        self.assertIn("cache_control", content[1])


class TestSkipCacheWrite(unittest.TestCase):
    def test_skip_cache_write_moves_marker_one_position_earlier(self) -> None:
        msgs = [_user("a"), _assistant("b"), _user("c")]
        out = add_cache_breakpoints(msgs, skip_cache_write=True)

        # First message unchanged.
        self.assertEqual(out[0], _user("a"))
        # Second message NOW carries the marker.
        second_content = out[1]["content"]
        self.assertIsInstance(second_content, list)
        self.assertIn("cache_control", second_content[-1])
        # Third message unchanged.
        self.assertEqual(out[2], _user("c"))

    def test_skip_cache_write_with_single_message_emits_no_marker(self) -> None:
        """TS-aligned: with skip_cache_write and only one message, the
        marker index computes to -1 and nothing matches it inside the .map().
        Mirrors TS at claude.ts:3133 where ``markerIndex = messages.length - 2``
        becomes -1 for a single-message array — fire-and-forget forks of a
        single user message have no shared-prefix point to cache against
        anyway.
        """
        msgs = [_user("only")]
        out = add_cache_breakpoints(msgs, skip_cache_write=True)
        # Message unchanged — no cache_control attached anywhere.
        self.assertEqual(out, msgs)


class TestImmutability(unittest.TestCase):
    def test_caller_messages_not_mutated(self) -> None:
        msgs = [_user("a"), _user("b")]
        snapshot = copy.deepcopy(msgs)
        add_cache_breakpoints(msgs)
        # Caller's list and its contents are untouched.
        self.assertEqual(msgs, snapshot)

    def test_block_list_clone_isolation(self) -> None:
        block = {"type": "text", "text": "x"}
        msg = {"role": "user", "content": [block]}
        out = add_cache_breakpoints([msg])
        # Marker landed on the cloned block, not the caller's.
        self.assertNotIn("cache_control", block)
        self.assertIn("cache_control", out[0]["content"][-1])


if __name__ == "__main__":
    unittest.main()
