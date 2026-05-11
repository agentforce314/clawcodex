"""Tests for ``src/state/session_start.py`` — Phase 3.2 wiring."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import pytest

from src.state.cache_state import (
    get_beta_header_latches,
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


if __name__ == "__main__":
    unittest.main()
