"""Tests for ``src/state/cache_state.py`` — WI-2.1 sticky latches.

The chapter's "Sticky Latch Fields" section motivates these tests: each
latch protects ~50-70K tokens of cached prompt from mid-session toggle
busts. The cost of getting the truth-table wrong is paid in cache misses
on every subsequent turn until the session ends.
"""

from __future__ import annotations

import unittest


class TestBetaHeaderLatchesDefaults(unittest.TestCase):
    """Initial latch state — None / False / [] before any wiring fires."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def test_initial_eligibility_is_none_not_false(self):
        """``None`` distinguishes 'not yet evaluated' from 'evaluated to False'."""
        from src.state.cache_state import get_beta_header_latches
        self.assertIsNone(get_beta_header_latches().prompt_cache_1h_eligible)

    def test_initial_allowlist_is_empty(self):
        from src.state.cache_state import get_beta_header_latches
        self.assertEqual(get_beta_header_latches().prompt_cache_1h_allowlist, [])

    def test_initial_toggle_latches_are_false(self):
        from src.state.cache_state import get_beta_header_latches
        latches = get_beta_header_latches()
        self.assertFalse(latches.fast_mode_header_latched)
        self.assertFalse(latches.afk_mode_header_latched)
        self.assertFalse(latches.cache_editing_header_latched)
        self.assertFalse(latches.thinking_clear_latched)


class TestEvaluatePromptCache1hEligibility(unittest.TestCase):
    """Truth table for the 1h-eligibility decision (TS claude.ts:420-425)."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def test_ant_user_is_eligible_regardless_of_subscriber_or_overage(self):
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        result = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=True,
        )
        self.assertTrue(result)

    def test_subscriber_not_overage_is_eligible(self):
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        result = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=False,
        )
        self.assertTrue(result)

    def test_subscriber_using_overage_is_not_eligible(self):
        """The whole point of the latch — overage flips don't bust the cache."""
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        result = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=True,
        )
        self.assertFalse(result)

    def test_non_subscriber_non_ant_is_not_eligible(self):
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        result = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=False, is_using_overage=False,
        )
        self.assertFalse(result)


class TestEligibilityLatchIsSticky(unittest.TestCase):
    """First-call evaluation; subsequent calls return latched value regardless of inputs."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def test_subsequent_call_returns_latched_true_even_when_inputs_say_false(self):
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        first = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        self.assertTrue(first)
        # Now overage is True; without latching this would flip to False.
        # WITH latching, the answer stays True.
        second = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=True,
        )
        self.assertTrue(second, "Latch must be sticky — overage flip cannot un-latch")

    def test_subsequent_call_returns_latched_false_even_when_inputs_say_true(self):
        from src.state.cache_state import evaluate_prompt_cache_1h_eligibility
        first = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=False, is_using_overage=False,
        )
        self.assertFalse(first)
        # Now flip every input to make the user eligible. Latch holds at False.
        second = evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=True, is_using_overage=False,
        )
        self.assertFalse(second, "Latch must be sticky — eligibility cannot up-latch")


class TestShould1hCacheTtl(unittest.TestCase):
    """Per-call decision combining latch + allowlist."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def test_returns_false_before_eligibility_is_evaluated(self):
        from src.state.cache_state import should_1h_cache_ttl
        # No prior call to evaluate_prompt_cache_1h_eligibility; latch is None.
        self.assertFalse(should_1h_cache_ttl("main"))

    def test_returns_false_when_eligible_but_query_source_not_in_allowlist(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, should_1h_cache_ttl,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        # Allowlist is empty by default — even an eligible user gets 5m.
        self.assertFalse(should_1h_cache_ttl("main"))

    def test_returns_true_when_eligible_and_in_allowlist(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
            should_1h_cache_ttl,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        # Populate the allowlist (would normally come from GrowthBook config).
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main", "memdir_relevance"]
        self.assertTrue(should_1h_cache_ttl("main"))

    def test_returns_false_for_unlisted_source_even_when_eligible(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
            should_1h_cache_ttl,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False,
        )
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main"]
        self.assertFalse(should_1h_cache_ttl("auto_mode"))

    def test_returns_false_when_in_allowlist_but_not_eligible(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility, get_beta_header_latches,
            should_1h_cache_ttl,
        )
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=False, is_using_overage=False,
        )
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main"]
        self.assertFalse(should_1h_cache_ttl("main"))


class TestToggleLatchesAreSticky(unittest.TestCase):
    """Once any toggle latch flips True, it stays True for the session."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def test_fast_mode_latch_setting_is_sticky(self):
        """Setting the latch True then attempting to write False is allowed
        only via reset_for_test_only — there is no public re-evaluation API.
        """
        from src.state.cache_state import get_beta_header_latches
        latches = get_beta_header_latches()
        self.assertFalse(latches.fast_mode_header_latched)
        latches.fast_mode_header_latched = True
        # No public API to flip back; production code never does.
        self.assertTrue(latches.fast_mode_header_latched)

    def test_reset_for_test_only_wipes_state(self):
        from src.state.cache_state import (
            get_beta_header_latches, reset_for_test_only,
        )
        latches = get_beta_header_latches()
        latches.fast_mode_header_latched = True
        latches.prompt_cache_1h_eligible = True
        reset_for_test_only()
        self.assertFalse(get_beta_header_latches().fast_mode_header_latched)
        self.assertIsNone(get_beta_header_latches().prompt_cache_1h_eligible)


class TestFastModeWiring(unittest.TestCase):
    """``is_fast_mode_enabled()`` latches ``fast_mode_header_latched`` on first True."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()

    def tearDown(self):
        # Reset env var so neighboring tests aren't affected.
        import os
        os.environ.pop("CLAUDE_FAST_MODE", None)

    def test_first_true_result_latches_the_header_field(self):
        """First call returning True triggers the latch."""
        import os
        from src.state.cache_state import get_beta_header_latches
        from src.utils.fast_mode import is_fast_mode_enabled

        os.environ["CLAUDE_FAST_MODE"] = "1"
        self.assertFalse(get_beta_header_latches().fast_mode_header_latched)
        result = is_fast_mode_enabled()
        self.assertTrue(result)
        self.assertTrue(
            get_beta_header_latches().fast_mode_header_latched,
            "First True result must latch the header field",
        )

    def test_subsequent_disable_does_not_clear_latch(self):
        """Sticky-on: even after fast mode is disabled, latch stays True."""
        import os
        from src.state.cache_state import get_beta_header_latches
        from src.utils.fast_mode import is_fast_mode_enabled

        os.environ["CLAUDE_FAST_MODE"] = "1"
        is_fast_mode_enabled()  # latches
        os.environ["CLAUDE_FAST_MODE"] = "0"
        is_fast_mode_enabled()  # returns False but latch stays True
        self.assertTrue(
            get_beta_header_latches().fast_mode_header_latched,
            "Latch must be sticky-on across mid-session disable",
        )

    def test_false_result_does_not_latch(self):
        from src.state.cache_state import get_beta_header_latches
        from src.utils.fast_mode import is_fast_mode_enabled

        # No env var; default config_value is None → returns False.
        result = is_fast_mode_enabled()
        self.assertFalse(result)
        self.assertFalse(
            get_beta_header_latches().fast_mode_header_latched,
            "Latch should not flip on a False result",
        )


class TestIsFirstPartyProvider(unittest.TestCase):
    """``is_first_party_provider`` gates global-scope emission (used by WI-2.3)."""

    def test_anthropic_with_no_base_url_is_first_party(self):
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import is_first_party_provider
        provider = AnthropicProvider(api_key="test")
        self.assertTrue(is_first_party_provider(provider))

    def test_anthropic_with_custom_base_url_is_not_first_party(self):
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import is_first_party_provider
        provider = AnthropicProvider(
            api_key="test", base_url="https://proxy.example.com"
        )
        self.assertFalse(is_first_party_provider(provider))

    def test_non_anthropic_provider_is_not_first_party(self):
        from src.state.cache_state import is_first_party_provider
        # Use a stub object that's NOT an AnthropicProvider.
        class StubProvider:
            pass
        self.assertFalse(is_first_party_provider(StubProvider()))


class TestShouldUseGlobalCacheScope(unittest.TestCase):
    """WI-2.3 — global-scope decision combining provider + MCP + env-gate.

    Per chapter line 91, ``scope: 'global'`` may be emitted only when
    ALL preconditions hold: first-party Anthropic, no MCP tools loaded,
    and the opt-in env var. The env-gate defaults to OFF (safe default)
    until staging-side verification confirms the API accepts the field
    from this client. (Per A13/R7: SDK passes through the field; API-side
    acceptance is the unverified piece.)
    """

    def setUp(self):
        import os
        from src.state.cache_state import reset_for_test_only
        reset_for_test_only()
        os.environ.pop("CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", None)

    def tearDown(self):
        import os
        os.environ.pop("CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", None)

    def test_default_is_disabled_without_env_var(self):
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import should_use_global_cache_scope
        provider = AnthropicProvider(api_key="test")
        self.assertFalse(
            should_use_global_cache_scope(
                provider=provider, has_mcp_tools=False,
            ),
            "Default-OFF: env-gated opt-in keeps prod traffic safe",
        )

    def test_enabled_when_all_preconditions_hold(self):
        import os
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import should_use_global_cache_scope
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        provider = AnthropicProvider(api_key="test")
        self.assertTrue(
            should_use_global_cache_scope(
                provider=provider, has_mcp_tools=False,
            ),
        )

    def test_disabled_when_mcp_tools_present(self):
        """Per chapter line 91: MCP schemas are per-user, can't share globally."""
        import os
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import should_use_global_cache_scope
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        provider = AnthropicProvider(api_key="test")
        self.assertFalse(
            should_use_global_cache_scope(
                provider=provider, has_mcp_tools=True,
            ),
        )

    def test_disabled_when_provider_is_third_party(self):
        """Custom base_url indicates a proxy/self-hosted endpoint."""
        import os
        from src.providers.anthropic_provider import AnthropicProvider
        from src.state.cache_state import should_use_global_cache_scope
        os.environ["CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE"] = "1"
        provider = AnthropicProvider(
            api_key="test", base_url="https://proxy.example.com",
        )
        self.assertFalse(
            should_use_global_cache_scope(
                provider=provider, has_mcp_tools=False,
            ),
        )


class TestPromptCache1hPlainGetters(unittest.TestCase):
    """WI-11 (Phase 2 bootstrap parity): plain getters for the latched 1h
    eligibility & allowlist. See bootstrap-gap-analysis.md §1.4 — no
    plain setter exposed (writes must go through the latching evaluator
    or direct dataclass mutation)."""

    def setUp(self):
        from src.state.cache_state import reset_for_test_only

        reset_for_test_only()

    def test_get_prompt_cache_1h_eligible_returns_none_initially(self):
        from src.state.cache_state import get_prompt_cache_1h_eligible

        self.assertIsNone(get_prompt_cache_1h_eligible())

    def test_get_prompt_cache_1h_eligible_returns_latched_true(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility,
            get_prompt_cache_1h_eligible,
        )

        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=False,
        )
        self.assertIs(get_prompt_cache_1h_eligible(), True)

    def test_get_prompt_cache_1h_eligible_returns_latched_false(self):
        from src.state.cache_state import (
            evaluate_prompt_cache_1h_eligibility,
            get_prompt_cache_1h_eligible,
        )

        # is_using_overage=True forces False for subscribers
        evaluate_prompt_cache_1h_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=True,
        )
        self.assertIs(get_prompt_cache_1h_eligible(), False)

    def test_get_prompt_cache_1h_allowlist_returns_empty_list_by_default(self):
        from src.state.cache_state import get_prompt_cache_1h_allowlist

        self.assertEqual(get_prompt_cache_1h_allowlist(), [])

    def test_get_prompt_cache_1h_allowlist_returns_copy(self):
        """Mutating the returned list must not leak into the singleton."""
        from src.state.cache_state import (
            get_beta_header_latches,
            get_prompt_cache_1h_allowlist,
        )

        # Seed the singleton allowlist via direct dataclass attribute write
        get_beta_header_latches().prompt_cache_1h_allowlist = ["main_loop"]
        snapshot = get_prompt_cache_1h_allowlist()
        self.assertEqual(snapshot, ["main_loop"])
        snapshot.append("attacker-injected")
        # Singleton allowlist unchanged
        self.assertEqual(
            get_beta_header_latches().prompt_cache_1h_allowlist,
            ["main_loop"],
        )


if __name__ == "__main__":
    unittest.main()
