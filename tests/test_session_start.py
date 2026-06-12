"""Tests for ``src/state/session_start.py`` — Phase 3.2 wiring."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import pytest

from src.state.cache_state import (
    get_beta_header_latches,
    reset_for_test_only,
    should_1h_cache_ttl,
)
from src.state.session_start import (
    initialize_prompt_cache_eligibility,
    reset_eligibility_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_latches():
    reset_eligibility_for_tests()
    yield
    reset_eligibility_for_tests()


class TestInitializePromptCacheEligibility(unittest.TestCase):
    def test_explicit_inputs_take_precedence_over_env(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_USER_TYPE": "ant"}, clear=False):
            result = initialize_prompt_cache_eligibility(
                is_ant_user=False, is_subscriber=False, is_using_overage=False
            )
            self.assertFalse(result)

    def test_env_vars_drive_defaults(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_USER_TYPE": "ant",
                "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER": "",
                "CLAUDE_CODE_IS_USING_OVERAGE": "",
            },
            clear=False,
        ):
            result = initialize_prompt_cache_eligibility()
            self.assertTrue(result)

    def test_subscriber_not_overage_yields_eligible(self) -> None:
        result = initialize_prompt_cache_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=False
        )
        self.assertTrue(result)

    def test_subscriber_with_overage_yields_not_eligible(self) -> None:
        result = initialize_prompt_cache_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=True
        )
        self.assertFalse(result)

    def test_default_inputs_yield_not_eligible(self) -> None:
        """Without env vars and without explicit args, the latch resolves
        to False — the safe default."""
        # Clear any test-environment env vars first
        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_USER_TYPE": "",
                "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER": "",
                "CLAUDE_CODE_IS_USING_OVERAGE": "",
            },
            clear=False,
        ):
            result = initialize_prompt_cache_eligibility()
            self.assertFalse(result)

    def test_latch_is_one_shot(self) -> None:
        """Once latched, subsequent calls return the same value
        regardless of new inputs."""
        first = initialize_prompt_cache_eligibility(
            is_ant_user=True, is_subscriber=False, is_using_overage=False
        )
        self.assertTrue(first)

        # Now call again with inputs that *would* yield False
        second = initialize_prompt_cache_eligibility(
            is_ant_user=False, is_subscriber=False, is_using_overage=True
        )
        # Latch is sticky: still returns True
        self.assertTrue(second)

    def test_initialize_settles_state_for_should_1h_cache_ttl(self) -> None:
        """After session-start initialization, ``should_1h_cache_ttl``
        sees a settled value (True or False, not None)."""
        initialize_prompt_cache_eligibility(
            is_ant_user=False, is_subscriber=True, is_using_overage=False
        )
        latches = get_beta_header_latches()
        self.assertIs(latches.prompt_cache_1h_eligible, True)

        # ``should_1h_cache_ttl`` still returns False here because the
        # allowlist is empty — that's a separate WI (populating the
        # allowlist from config).
        self.assertFalse(should_1h_cache_ttl("agent"))

        # But once the allowlist has the query source, it returns True.
        latches.prompt_cache_1h_allowlist.append("agent")
        self.assertTrue(should_1h_cache_ttl("agent"))


# ---------------------------------------------------------------------------
# #285 — config-backed 1h allowlist + session-start wiring
# ---------------------------------------------------------------------------


class TestPopulateAllowlist(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_test_only()

    def tearDown(self) -> None:
        reset_for_test_only()

    def test_populates_once_and_is_sticky(self) -> None:
        from src.state.cache_state import (
            get_prompt_cache_1h_allowlist,
            populate_prompt_cache_1h_allowlist,
        )

        assert populate_prompt_cache_1h_allowlist(["repl_main_thread"]) is True
        assert get_prompt_cache_1h_allowlist() == ["repl_main_thread"]
        # Sticky: a second population mid-session is refused.
        assert populate_prompt_cache_1h_allowlist(["other"]) is False
        assert get_prompt_cache_1h_allowlist() == ["repl_main_thread"]

    def test_empty_and_garbage_entries_rejected(self) -> None:
        from src.state.cache_state import (
            get_prompt_cache_1h_allowlist,
            populate_prompt_cache_1h_allowlist,
        )

        assert populate_prompt_cache_1h_allowlist([]) is False
        assert populate_prompt_cache_1h_allowlist(["  ", ""]) is False
        assert get_prompt_cache_1h_allowlist() == []
        assert populate_prompt_cache_1h_allowlist(["  a  ", "", "b"]) is True
        assert get_prompt_cache_1h_allowlist() == ["a", "b"]


class TestInitializePromptCacheState(unittest.TestCase):
    def setUp(self) -> None:
        reset_for_test_only()

    def tearDown(self) -> None:
        reset_for_test_only()

    def test_env_sources_and_eligibility_activate_1h(self) -> None:
        from src.state.cache_state import should_1h_cache_ttl
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER": "1",
                "CLAWCODEX_PROMPT_CACHE_1H_SOURCES": "repl_main_thread, sdk",
            },
        ):
            initialize_prompt_cache_state()
        assert should_1h_cache_ttl("repl_main_thread") is True
        assert should_1h_cache_ttl("sdk") is True
        assert should_1h_cache_ttl("agent_explore") is False

    def test_sources_without_eligibility_stay_5m(self) -> None:
        from src.state.cache_state import should_1h_cache_ttl
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(
            os.environ,
            {"CLAWCODEX_PROMPT_CACHE_1H_SOURCES": "repl_main_thread"},
            clear=False,
        ):
            os.environ.pop("CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER", None)
            os.environ.pop("CLAUDE_CODE_USER_TYPE", None)
            initialize_prompt_cache_state()
        assert should_1h_cache_ttl("repl_main_thread") is False

    def test_settings_sources_used_when_env_absent(self) -> None:
        from types import SimpleNamespace

        from src.state.cache_state import get_prompt_cache_1h_allowlist
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAWCODEX_PROMPT_CACHE_1H_SOURCES", None)
            with mock.patch(
                "src.settings.settings.get_settings",
                return_value=SimpleNamespace(
                    prompt_cache_1h_sources=["repl_main_thread"]
                ),
            ):
                initialize_prompt_cache_state()
        assert get_prompt_cache_1h_allowlist() == ["repl_main_thread"]

    def test_no_config_stays_dormant(self) -> None:
        from src.state.cache_state import (
            get_prompt_cache_1h_allowlist,
            should_1h_cache_ttl,
        )
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAWCODEX_PROMPT_CACHE_1H_SOURCES", None)
            initialize_prompt_cache_state()
        assert get_prompt_cache_1h_allowlist() == []
        assert should_1h_cache_ttl("repl_main_thread") is False

    def test_idempotent(self) -> None:
        from src.state.cache_state import get_prompt_cache_1h_allowlist
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER": "1",
                "CLAWCODEX_PROMPT_CACHE_1H_SOURCES": "repl_main_thread",
            },
        ):
            initialize_prompt_cache_state()
            initialize_prompt_cache_state()
        assert get_prompt_cache_1h_allowlist() == ["repl_main_thread"]

    def test_empty_env_var_is_a_kill_switch(self) -> None:
        # CLAWCODEX_PROMPT_CACHE_1H_SOURCES set-but-empty disables 1h
        # even when settings configure sources (env wins absolutely).
        from types import SimpleNamespace

        from src.state.cache_state import get_prompt_cache_1h_allowlist
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(
            os.environ, {"CLAWCODEX_PROMPT_CACHE_1H_SOURCES": ""}
        ):
            with mock.patch(
                "src.settings.settings.get_settings",
                return_value=SimpleNamespace(
                    prompt_cache_1h_sources=["repl_main_thread"]
                ),
            ):
                initialize_prompt_cache_state()
        assert get_prompt_cache_1h_allowlist() == []

    def test_1h_recovers_after_clear(self) -> None:
        # /clear and /compact reset the latch singleton via
        # clear_beta_header_latches; the lazy re-init in
        # should_1h_cache_ttl must re-evaluate instead of silently
        # downgrading the rest of the session to 5m.
        from src.state.cache_state import (
            clear_beta_header_latches,
            should_1h_cache_ttl,
        )
        from src.state.session_start import initialize_prompt_cache_state

        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER": "1",
                "CLAWCODEX_PROMPT_CACHE_1H_SOURCES": "repl_main_thread",
            },
        ):
            initialize_prompt_cache_state()
            assert should_1h_cache_ttl("repl_main_thread") is True
            clear_beta_header_latches()
            # Lazy re-init at the consumer recovers the 1h decision.
            assert should_1h_cache_ttl("repl_main_thread") is True



if __name__ == "__main__":
    unittest.main()
