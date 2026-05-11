"""Tests for the section factories from ch04-api-layer refactor (Phase A).

Mirrors the TS contract in ``constants/systemPromptSections.ts``: cacheable
sections route through ``system_prompt_section``; cache-breaking sections
must route through ``DANGEROUS_uncachedSystemPromptSection`` with a
non-empty reason. The factory is the *only* surface that should be used
when authoring new prompt sections — direct ``SystemPromptSection(...)``
construction bypasses the convention.
"""
from __future__ import annotations

import unittest

from src.context_system.system_prompt_cache import (
    CacheScope,
    DANGEROUS_uncachedSystemPromptSection,
    SystemPromptSection,
    system_prompt_section,
)


class TestSystemPromptSectionFactory(unittest.TestCase):
    def test_safe_factory_defaults(self) -> None:
        section = system_prompt_section("intro", content="hello world")
        self.assertIsInstance(section, SystemPromptSection)
        self.assertEqual(section.id, "intro")
        self.assertEqual(section.content, "hello world")
        self.assertEqual(section.cache_scope, CacheScope.SESSION)
        self.assertFalse(section.cache_break)
        self.assertIsNone(section.reason)

    def test_safe_factory_explicit_scope_and_order(self) -> None:
        section = system_prompt_section(
            "memory",
            content="mem",
            cache_scope=CacheScope.GLOBAL,
            order=10,
        )
        self.assertEqual(section.cache_scope, CacheScope.GLOBAL)
        self.assertEqual(section.order, 10)
        self.assertFalse(section.cache_break)


class TestDangerousFactory(unittest.TestCase):
    def test_dangerous_factory_sets_cache_break_and_reason(self) -> None:
        section = DANGEROUS_uncachedSystemPromptSection(
            "mcp",
            content="mcp instructions",
            reason="MCP servers connect/disconnect between turns",
        )
        self.assertTrue(section.cache_break)
        self.assertEqual(section.reason, "MCP servers connect/disconnect between turns")
        self.assertEqual(section.cache_scope, CacheScope.REQUEST)

    def test_dangerous_factory_strips_reason_whitespace(self) -> None:
        section = DANGEROUS_uncachedSystemPromptSection(
            "x",
            content="x",
            reason="   the cache changes per turn   ",
        )
        self.assertEqual(section.reason, "the cache changes per turn")

    def test_dangerous_factory_rejects_empty_reason(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            DANGEROUS_uncachedSystemPromptSection(
                "x", content="x", reason="",
            )
        self.assertIn("reason", str(ctx.exception).lower())

    def test_dangerous_factory_rejects_whitespace_reason(self) -> None:
        with self.assertRaises(ValueError):
            DANGEROUS_uncachedSystemPromptSection(
                "x", content="x", reason="   \t \n  ",
            )

    def test_dangerous_factory_default_scope_is_request(self) -> None:
        """REQUEST scope is the default because cache-breaking sections
        cannot share the SESSION-scope cache slot — they recompute per turn.
        """
        section = DANGEROUS_uncachedSystemPromptSection(
            "x", content="x", reason="varies turn-to-turn",
        )
        self.assertEqual(section.cache_scope, CacheScope.REQUEST)


if __name__ == "__main__":
    unittest.main()
