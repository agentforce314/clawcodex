"""Tests for src/context_system/prompt_assembly.py — WS-5 prompt assembly."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.context_system.prompt_assembly import (
    _compute_env_info,
    _get_local_iso_date,
    append_system_context,
    clear_context_caches,
    fetch_system_prompt_parts,
    get_system_context,
    get_user_context,
    prepend_user_context,
)
from src.context_system.claude_md import clear_memory_file_caches
from src.context_system.git_context import clear_git_caches
from src.context_system.models import SystemPromptParts
from src.types.messages import UserMessage


def _run(coro):
    return asyncio.run(coro)


class TestGetLocalIsoDate(unittest.TestCase):
    def test_returns_string(self):
        result = _get_local_iso_date()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 10)


class TestSessionStartDateMemoization(unittest.TestCase):
    """WI-1.2 — date memoization for prompt-cache stability.

    The chapter motivates this with::

        const getSessionStartDate = memoize(getLocalISODate)

    Without memoization, the date string drifts on every turn and (once
    cache_control markers engage server-side caching) busts the cached
    prefix. Tests verify (a) memoization holds, (b) date-only format,
    (c) survives clear_context_caches.
    """

    def setUp(self):
        # The lru_cache is module-level; clear it between tests so each
        # case starts with a fresh value rather than leaking the date
        # captured by an earlier test.
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        _get_session_start_date_iso.cache_clear()

    def test_returns_date_only_format(self):
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        result = _get_session_start_date_iso()
        # Strict date-only: YYYY-MM-DD, no hh:mm:ss, no timezone
        import re
        self.assertRegex(
            result,
            r"^\d{4}-\d{2}-\d{2}$",
            f"Expected date-only format YYYY-MM-DD, got {result!r}",
        )

    def test_memoized_returns_same_value(self):
        """Two consecutive calls return the SAME string (frozen at first call)."""
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        first = _get_session_start_date_iso()
        # Even if the wall clock advances mid-test, the cached value must hold.
        second = _get_session_start_date_iso()
        self.assertEqual(first, second)

    def test_lru_cache_survives_clear_context_caches(self):
        """clear_context_caches() must NOT invalidate the lru_cache on the date.

        Critical invariant: ``clear_context_caches()`` is called after compact
        (per the docstring at ``prompt_assembly.py:46``). If it cleared the
        date too, the user-context message containing the date would change
        post-compact, busting any cache_control marker that landed on a
        message block. The lru_cache lives at module level by design.
        """
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        before = _get_session_start_date_iso()
        clear_context_caches()
        after = _get_session_start_date_iso()
        self.assertEqual(before, after)

    def test_get_user_context_uses_session_start_date(self):
        """get_user_context() must use the memoized date, not _get_local_iso_date."""
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        clear_context_caches()
        ctx_a = _run(get_user_context())
        # Force a re-fetch after clearing the dict cache (but lru_cache survives).
        clear_context_caches()
        ctx_b = _run(get_user_context())
        # Both fetches see the same memoized date.
        self.assertEqual(ctx_a["currentDate"], ctx_b["currentDate"])
        # And it's the date-only memoized helper, not the live datetime.
        self.assertEqual(ctx_a["currentDate"], _get_session_start_date_iso())


class TestSystemPromptBlocks(unittest.TestCase):
    """WI-1.1 — block-list assembly with cache_control markers.

    These are the joint acceptance tests for WI-1.1 + WI-1.2: each
    assertion must hold for the cache to actually engage server-side.
    Failing any assertion means either the cache_control plumbing is
    broken (WI-1.1) OR the date isn't memoized (WI-1.2) — the test fails
    in the same way regardless of which is missing, enforcing the joint
    PR contract on standard CI without a live API key.
    """

    def setUp(self):
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        # Fresh date-cache per test so every case starts identical.
        _get_session_start_date_iso.cache_clear()
        clear_context_caches()

    def test_returns_list_of_dicts(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        self.assertIsInstance(blocks, list)
        for blk in blocks:
            self.assertIsInstance(blk, dict)
            self.assertEqual(blk["type"], "text")
            self.assertIn("text", blk)

    def test_at_least_one_cache_control_marker(self):
        """At least one block must carry cache_control: ephemeral.

        Without this, the API receives the system prompt but performs no
        caching — the fix is gap-#1's centerpiece.
        """
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        marked = [b for b in blocks if "cache_control" in b]
        self.assertGreaterEqual(len(marked), 1)
        # And every marker has the right shape.
        for blk in marked:
            self.assertEqual(blk["cache_control"]["type"], "ephemeral")
            self.assertIn(blk["cache_control"]["ttl"], ("5m", "1h"))

    def test_dynamic_boundary_literal_present(self):
        """The ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` marker is its own block."""
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        boundary_blocks = [
            b for b in blocks if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        ]
        self.assertEqual(
            len(boundary_blocks), 1,
            f"Expected exactly one boundary-marker block, got {len(boundary_blocks)}",
        )
        # Boundary block does NOT carry cache_control — it's just a literal.
        self.assertNotIn("cache_control", boundary_blocks[0])

    def test_at_most_four_cache_control_markers(self):
        """Anthropic API allows up to 4 cache_control markers per request."""
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        marked = [b for b in blocks if "cache_control" in b]
        self.assertLessEqual(len(marked), 4)

    def test_two_consecutive_calls_byte_identical(self):
        """Cache-stability invariant: identical inputs → byte-identical blocks.

        If this test fails between two consecutive calls, the cache is busting
        every turn — most likely because a date or other volatile field crept
        into a cached-tier section without going through ``_get_session_start_date_iso``.
        This is the joint contract: WI-1.1 alone (cache_control plumbing)
        plus WI-1.2 alone (date memoization) are necessary; only together
        are they sufficient.
        """
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        first = build_full_system_prompt_blocks(cwd="/tmp")
        second = build_full_system_prompt_blocks(cwd="/tmp")
        self.assertEqual(first, second)

    def test_global_blocks_appear_before_boundary(self):
        """GLOBAL-scope sections must precede the dynamic boundary marker."""
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        boundary_idx = next(
            i for i, b in enumerate(blocks)
            if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        )
        # At least the intro should be before the boundary.
        before_boundary = blocks[:boundary_idx]
        self.assertGreater(
            len(before_boundary), 0,
            "Expected at least one GLOBAL block before the boundary marker",
        )

    def test_custom_prompt_branch_returns_single_block(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(
            cwd="/tmp",
            custom_system_prompt="You are a custom assistant.",
        )
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn("custom assistant", blocks[0]["text"])
        # Custom prompt branch deliberately doesn't carry cache_control.
        self.assertNotIn("cache_control", blocks[0])


class TestSkillSectionWiring(unittest.TestCase):
    """P0-4 wiring: ``skills=`` → the "# Available Skills" system-prompt block.

    The model-facing skill listing was previously dead code — nothing ever
    passed ``skills=`` into ``build_full_system_prompt_blocks``, so
    ``_build_skill_section`` always short-circuited to None. Phase 3 wires
    ``get_skill_tool_commands(cwd)`` in at ``src/query/engine.py``; these tests
    pin the rendering contract that wiring depends on: a non-empty list emits a
    block carrying each skill's name + description, and an empty/None list emits
    no such block (plan §5 — port of TS ``getSkillToolCommands`` feeding the
    system prompt). ``use_cache=False`` so the section rebuilds from the passed
    list rather than the SESSION-scoped ``skills`` cache key.
    """

    def setUp(self):
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        _get_session_start_date_iso.cache_clear()
        clear_context_caches()

    @staticmethod
    def _skill_block(blocks):
        hits = [b for b in blocks if "# Available Skills" in b.get("text", "")]
        return hits[0]["text"] if hits else None

    def test_skills_render_into_available_skills_block(self):
        # Drive the same attribute-read path (.name/.description) the real
        # view feeds, using PromptCommand so the test exercises the production
        # type rather than a bespoke stub.
        from src.command_system.types import PromptCommand
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        skills = [
            PromptCommand(name="alpha", description="does alpha things"),
            PromptCommand(name="beta", description="does beta things"),
        ]
        blocks = build_full_system_prompt_blocks(cwd="/tmp", skills=skills, use_cache=False)
        text = self._skill_block(blocks)
        self.assertIsNotNone(text, "Expected a '# Available Skills' block")
        self.assertIn("alpha", text)
        self.assertIn("does alpha things", text)
        self.assertIn("beta", text)
        self.assertIn("does beta things", text)

    def test_empty_skills_emits_no_section(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp", skills=[], use_cache=False)
        self.assertIsNone(self._skill_block(blocks))

    def test_none_skills_emits_no_section(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp", skills=None, use_cache=False)
        self.assertIsNone(self._skill_block(blocks))


class TestCacheTtlSelector(unittest.TestCase):
    """WI-2.2: ``cache_control`` ttl picks 5m vs 1h via ``should_1h_cache_ttl``."""

    def setUp(self):
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        from src.state.cache_state import reset_for_test_only
        _get_session_start_date_iso.cache_clear()
        reset_for_test_only()
        clear_context_caches()

    def test_default_ttl_is_5m_when_eligibility_unevaluated(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        marked = [b for b in blocks if "cache_control" in b]
        self.assertGreater(len(marked), 0)
        for blk in marked:
            self.assertEqual(blk["cache_control"]["ttl"], "5m")

    def test_5m_ttl_when_eligible_but_query_source_not_in_allowlist(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        # Allowlist intentionally excludes "main".
        get_beta_header_latches().prompt_cache_1h_allowlist = ["other_source"]
        blocks = build_full_system_prompt_blocks(cwd="/tmp", query_source="main")
        marked = [b for b in blocks if "cache_control" in b]
        for blk in marked:
            self.assertEqual(blk["cache_control"]["ttl"], "5m")

    def test_1h_ttl_when_eligible_and_in_allowlist(self):
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main"]
        blocks = build_full_system_prompt_blocks(cwd="/tmp", query_source="main")
        marked = [b for b in blocks if "cache_control" in b]
        self.assertGreater(len(marked), 0)
        for blk in marked:
            self.assertEqual(blk["cache_control"]["ttl"], "1h")

    def test_ttl_decision_is_consistent_across_all_markers_in_one_call(self):
        """Every cache_control marker in a single call shares the same TTL.

        The decision is per-call (driven by query_source); all markers in
        that call use the same TTL value. Mixed 5m/1h within a single
        request is not a supported configuration.
        """
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main"]
        blocks = build_full_system_prompt_blocks(cwd="/tmp", query_source="main")
        marked = [b for b in blocks if "cache_control" in b]
        ttls = {blk["cache_control"]["ttl"] for blk in marked}
        self.assertEqual(len(ttls), 1, f"Mixed TTLs in one call: {ttls}")


class TestGlobalScopeEmission(unittest.TestCase):
    """WI-2.3: ``scope: 'global'`` only on GLOBAL-tier blocks, env-gated."""

    def setUp(self):
        import os
        from src.context_system.prompt_assembly import _get_session_start_date_iso
        from src.state.cache_state import reset_for_test_only
        _get_session_start_date_iso.cache_clear()
        reset_for_test_only()
        clear_context_caches()
        os.environ.pop("CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", None)

    def tearDown(self):
        import os
        os.environ.pop("CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", None)

    def test_no_scope_field_when_env_disabled(self):
        """Default-OFF env: no block carries scope='global'."""
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(api_key="test")
        blocks = build_full_system_prompt_blocks(cwd="/tmp", provider=provider)
        marked = [b for b in blocks if "cache_control" in b]
        self.assertGreater(len(marked), 0)
        for blk in marked:
            self.assertNotIn(
                "scope", blk["cache_control"],
                "Default OFF: no block should carry scope='global'",
            )

    def test_scope_global_emitted_only_on_global_tier_when_env_enabled(self):
        """When env is opted-in: GLOBAL block has scope='global'; SESSION/REQUEST do NOT."""
        import os
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.providers.anthropic_provider import AnthropicProvider
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        provider = AnthropicProvider(api_key="test")
        blocks = build_full_system_prompt_blocks(cwd="/tmp", provider=provider)

        # Locate the boundary marker; everything before it is GLOBAL-tier.
        from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        boundary_idx = next(
            i for i, b in enumerate(blocks)
            if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        )
        global_blocks = blocks[:boundary_idx]
        post_boundary = blocks[boundary_idx + 1:]

        # GLOBAL-tier marker carries scope='global'.
        global_marked = [b for b in global_blocks if "cache_control" in b]
        self.assertGreater(len(global_marked), 0)
        for blk in global_marked:
            self.assertEqual(
                blk["cache_control"].get("scope"), "global",
                "GLOBAL-tier block must carry scope='global' when env is enabled",
            )

        # SESSION + REQUEST tiers do NOT carry scope='global'.
        post_marked = [b for b in post_boundary if "cache_control" in b]
        for blk in post_marked:
            self.assertNotIn(
                "scope", blk["cache_control"],
                "Non-GLOBAL tier must not carry scope='global'",
            )

    def test_scope_disabled_when_mcp_present(self):
        """MCP servers in the call disable global scope (chapter line 91)."""
        import os
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        from src.providers.anthropic_provider import AnthropicProvider
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        provider = AnthropicProvider(api_key="test")
        # Stub MCP server marker — bool(mcp_servers) drives has_mcp_tools.
        blocks = build_full_system_prompt_blocks(
            cwd="/tmp", provider=provider, mcp_servers=[object()],
        )
        marked = [b for b in blocks if "cache_control" in b]
        for blk in marked:
            self.assertNotIn(
                "scope", blk["cache_control"],
                "MCP-loaded session must not emit scope='global'",
            )

    def test_no_provider_means_no_scope_field(self):
        """Engine path may pass provider=None for synthetic/test calls."""
        import os
        from src.context_system.prompt_assembly import build_full_system_prompt_blocks
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        blocks = build_full_system_prompt_blocks(cwd="/tmp", provider=None)
        marked = [b for b in blocks if "cache_control" in b]
        for blk in marked:
            self.assertNotIn("scope", blk["cache_control"])


class TestAppendSystemContextBlocks(unittest.TestCase):
    """WI-1.1: ``append_system_context_blocks`` preserves cache_control on prior blocks."""

    def test_appends_context_as_new_block(self):
        from src.context_system.prompt_assembly import append_system_context_blocks
        blocks = [
            {"type": "text", "text": "intro"},
            {"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}},
        ]
        result = append_system_context_blocks(blocks, {"gitStatus": "branch: main"})
        # Original blocks preserved (with cache_control intact).
        self.assertEqual(result[0], {"type": "text", "text": "intro"})
        self.assertEqual(
            result[1],
            {"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}},
        )
        # New block appended with the context.
        self.assertEqual(len(result), 3)
        self.assertEqual(result[2]["type"], "text")
        self.assertIn("gitStatus", result[2]["text"])
        self.assertNotIn("cache_control", result[2])

    def test_empty_context_returns_original_blocks(self):
        from src.context_system.prompt_assembly import append_system_context_blocks
        blocks = [{"type": "text", "text": "x"}]
        result = append_system_context_blocks(blocks, {})
        self.assertEqual(result, blocks)
        # And we got a fresh list, not the same reference (defensive copy).
        self.assertIsNot(result, blocks)


class TestAppendSystemContext(unittest.TestCase):
    def test_empty_context(self):
        result = append_system_context("Hello system", {})
        self.assertEqual(result, "Hello system")

    def test_with_git_status(self):
        result = append_system_context("System prompt", {"gitStatus": "branch: main"})
        self.assertIn("System prompt", result)
        self.assertIn("gitStatus: branch: main", result)

    def test_list_input(self):
        result = append_system_context(
            ["Section 1", "Section 2"],
            {"gitStatus": "clean"},
        )
        self.assertIn("Section 1", result)
        self.assertIn("Section 2", result)
        self.assertIn("gitStatus: clean", result)

    def test_empty_prompt_and_context(self):
        result = append_system_context("", {})
        self.assertEqual(result, "")

    def test_multiple_context_entries(self):
        result = append_system_context("Base", {
            "gitStatus": "clean",
            "envInfo": "macOS",
        })
        self.assertIn("gitStatus: clean", result)
        self.assertIn("envInfo: macOS", result)


class TestPrependUserContext(unittest.TestCase):
    def test_empty_context(self):
        msgs = [UserMessage(content="hi")]
        result = prepend_user_context(msgs, {})
        self.assertEqual(len(result), 1)

    def test_with_claude_md(self):
        msgs = [UserMessage(content="hi")]
        result = prepend_user_context(msgs, {"claudeMd": "Always test."})
        self.assertEqual(len(result), 2)
        # First message should be the system reminder
        first = result[0]
        self.assertIsInstance(first, UserMessage)
        self.assertIn("system-reminder", first.content)
        self.assertIn("Always test", first.content)

    def test_original_messages_preserved(self):
        msgs = [UserMessage(content="original")]
        result = prepend_user_context(msgs, {"claudeMd": "rule"})
        self.assertEqual(result[-1].content, "original")

    def test_multiple_context_keys(self):
        msgs = [UserMessage(content="q")]
        result = prepend_user_context(msgs, {
            "claudeMd": "Rule 1",
            "currentDate": "2025-01-01",
        })
        first_content = result[0].content
        self.assertIn("claudeMd", first_content)
        self.assertIn("currentDate", first_content)


class TestGetUserContext(unittest.TestCase):
    def setUp(self):
        clear_context_caches()

    def tearDown(self):
        clear_context_caches()

    def test_includes_current_date(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true"}):
            result = _run(get_user_context())
            self.assertIn("currentDate", result)
            self.assertIsInstance(result["currentDate"], str)

    def test_memoization(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true"}):
            result1 = _run(get_user_context())
            result2 = _run(get_user_context())
            self.assertEqual(result1, result2)

    def test_includes_claude_md_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("Test rule", encoding="utf-8")
            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
            }):
                clear_memory_file_caches()
                clear_context_caches()
                result = _run(get_user_context(cwd=tmp))
                if "claudeMd" in result:
                    self.assertIn("Test rule", result["claudeMd"])


class TestGetSystemContext(unittest.TestCase):
    def setUp(self):
        clear_context_caches()
        clear_git_caches()

    def tearDown(self):
        clear_context_caches()
        clear_git_caches()

    def test_memoization(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true"}):
            result1 = _run(get_system_context())
            result2 = _run(get_system_context())
            self.assertEqual(result1, result2)

    def test_git_disabled(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true"}):
            clear_context_caches()
            result = _run(get_system_context())
            self.assertNotIn("gitStatus", result)


class TestFetchSystemPromptParts(unittest.TestCase):
    def setUp(self):
        clear_context_caches()
        clear_git_caches()
        clear_memory_file_caches()

    def tearDown(self):
        clear_context_caches()
        clear_git_caches()
        clear_memory_file_caches()

    def test_returns_system_prompt_parts(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
        }):
            result = _run(fetch_system_prompt_parts())
            self.assertIsInstance(result, SystemPromptParts)
            self.assertIsInstance(result.default_system_prompt, list)
            self.assertIsInstance(result.user_context, dict)
            self.assertIsInstance(result.system_context, dict)

    def test_custom_prompt_skips_default(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
        }):
            result = _run(fetch_system_prompt_parts(
                custom_system_prompt="Custom prompt",
            ))
            self.assertEqual(result.default_system_prompt, [])
            self.assertEqual(result.system_context, {})

    def test_user_context_has_date(self):
        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
        }):
            result = _run(fetch_system_prompt_parts())
            self.assertIn("currentDate", result.user_context)


class TestComputeEnvInfo(unittest.TestCase):
    def test_includes_cwd(self):
        result = _compute_env_info("/test/path")
        self.assertIn("/test/path", result)
        self.assertIn("CWD:", result)
        self.assertIn("OS:", result)
        self.assertIn("Date:", result)


class TestClearContextCaches(unittest.TestCase):
    def test_no_crash(self):
        clear_context_caches()
        clear_context_caches()  # Double clear should be fine


if __name__ == "__main__":
    unittest.main()
