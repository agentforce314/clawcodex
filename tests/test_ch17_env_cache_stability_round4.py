"""ch17 round-4 — the # Environment block's date must be cache-stable.

The env block is REQUEST-scope and the REQUEST group's last block carries a
cache_control marker, so a per-second timestamp busted the REQUEST cache
breakpoint (1 of only 4) on every turn. It now uses the memoized date-only
helper. These tests pin that regression.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from src.context_system import prompt_assembly as pa


class TestEnvDateCacheStability(unittest.TestCase):
    def setUp(self):
        # The date helper is process-memoized; clear it so each test controls
        # the frozen value.
        pa._get_session_start_date_iso.cache_clear()

    def tearDown(self):
        pa._get_session_start_date_iso.cache_clear()

    def test_env_block_is_date_only_no_seconds(self):
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 15, 10, 8)
            section = pa._build_env_section("/x", False)
        self.assertIn("- Date: 2026-07-02", section.content)
        # No per-second wall clock (the thing that busted the cache).
        self.assertNotIn("T15:10", section.content)
        self.assertNotIn(":08", section.content)

    def test_env_block_byte_identical_across_clock_advance(self):
        # First build freezes the date; a later build (clock advanced by
        # hours) must produce a BYTE-IDENTICAL block → the REQUEST cache
        # breakpoint's prefix stays cacheable turn-over-turn.
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 15, 10, 8)
            s1 = pa._build_env_section("/x", False)
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 23, 59, 59)  # hours later
            s2 = pa._build_env_section("/x", False)
        self.assertEqual(s1.content, s2.content)

    def test_env_section_is_request_scope(self):
        # Guards the assumption that makes the timestamp cache-relevant.
        from src.context_system.system_prompt_cache import CacheScope
        section = pa._build_env_section("/x", False)
        self.assertEqual(section.cache_scope, CacheScope.REQUEST)

    def test_legacy_str_env_info_also_date_only(self):
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 15, 10, 8)
            info = pa._compute_env_info("/x")
        self.assertIn("Date: 2026-07-02", info)
        self.assertNotIn("T15:10", info)


class TestFullBlocksRequestBreakpointStable(unittest.TestCase):
    def setUp(self):
        pa._get_session_start_date_iso.cache_clear()

    def tearDown(self):
        pa._get_session_start_date_iso.cache_clear()

    def test_request_scope_env_stable_across_two_builds(self):
        # End-to-end: two full block-list builds with the clock advanced
        # between them must have byte-identical REQUEST-scope env text
        # (mirrors the facet's empirical probe — the only prior diff was the
        # env date's seconds).
        # Default assembly (no custom prompt, which would replace the blocks).
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 15, 10, 8)
            blocks1 = pa.build_full_system_prompt_blocks(cwd="/x")
        with patch.object(pa, "datetime") as m:
            m.now.return_value = datetime(2026, 7, 2, 15, 10, 30)
            blocks2 = pa.build_full_system_prompt_blocks(cwd="/x")

        env1 = [b for b in _texts(blocks1) if "# Environment" in b]
        env2 = [b for b in _texts(blocks2) if "# Environment" in b]
        self.assertTrue(env1, "env block present")
        self.assertEqual(env1, env2)  # byte-identical → cache prefix matches


def _texts(blocks) -> list[str]:
    out = []
    for b in blocks:
        if isinstance(b, dict):
            t = b.get("text")
            if isinstance(t, str):
                out.append(t)
    return out


if __name__ == "__main__":
    unittest.main()
