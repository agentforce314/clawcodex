"""Test the cache+latch reset that fires on /clear and /compact (Phase A)."""
from __future__ import annotations

import unittest

from src.context_system.system_prompt_cache import (
    CacheScope,
    clear_system_prompt_sections,
)
from src.state import cache_state as cs


class TestClearSystemPromptSections(unittest.TestCase):
    def setUp(self) -> None:
        cs.reset_for_test_only()
        # Isolate the process-global prompt-section cache: other tests that
        # exercise the TUI/headless cutover (build_effective_system_prompt →
        # build_full_system_prompt_blocks) populate it, and this test asserts
        # exact cache sizes. Clear it so collection order can't leak in.
        from src.context_system.prompt_assembly import get_system_prompt_cache
        get_system_prompt_cache().invalidate_all()

    def tearDown(self) -> None:
        cs.reset_for_test_only()
        from src.context_system.prompt_assembly import get_system_prompt_cache
        get_system_prompt_cache().invalidate_all()

    def test_clear_resets_all_toggle_latches(self) -> None:
        latches = cs.get_beta_header_latches()
        latches.fast_mode_header_latched = True
        latches.afk_mode_header_latched = True
        latches.cache_editing_header_latched = True
        latches.thinking_clear_latched = True
        latches.prompt_cache_1h_eligible = True

        clear_system_prompt_sections()

        fresh = cs.get_beta_header_latches()
        self.assertFalse(fresh.fast_mode_header_latched)
        self.assertFalse(fresh.afk_mode_header_latched)
        self.assertFalse(fresh.cache_editing_header_latched)
        self.assertFalse(fresh.thinking_clear_latched)
        self.assertIsNone(fresh.prompt_cache_1h_eligible)

    def test_clear_invalidates_prompt_cache(self) -> None:
        from src.context_system.prompt_assembly import get_system_prompt_cache

        cache = get_system_prompt_cache()
        # Populate the cache with three sections at distinct scopes so we
        # can distinguish a partial clear from a full clear.
        from src.context_system.system_prompt_cache import CacheScope
        cache.set("intro", "intro content", scope=CacheScope.GLOBAL)
        cache.set("env", "env content", scope=CacheScope.SESSION)
        cache.set("mcp", "mcp content", scope=CacheScope.REQUEST)
        self.assertEqual(cache.size, 3)

        clear_system_prompt_sections()

        # All entries gone — invalidate_all, not just one scope.
        self.assertEqual(cache.size, 0)
        self.assertIsNone(cache.get("intro"))
        self.assertIsNone(cache.get("env"))
        self.assertIsNone(cache.get("mcp"))

    def test_clear_is_safe_when_prompt_assembly_unavailable(self) -> None:
        """The function must not raise even when ``prompt_assembly`` cannot
        be imported (minimal test envs without context_system fully wired).
        /clear and /compact run unconditionally — they cannot afford to
        crash because an optional submodule is missing.
        """
        # We can't easily simulate the ImportError here, but the
        # implementation guards with ``except ImportError: pass`` so this
        # is at minimum a smoke test that no other exception classes leak.
        clear_system_prompt_sections()  # should not raise


if __name__ == "__main__":
    unittest.main()
