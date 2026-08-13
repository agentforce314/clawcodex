"""The /model picker's step 3: which effort levels a model actually accepts.

The list has to be the levels the model will really take, not the union
ladder ``/effort`` validates against — offering a level the model rejects is
fatal (a 400 on the effort level is retried nowhere), while omitting one
merely hides a choice.
"""

import pytest

from src.providers.effort_options import LADDER, effort_options
from src.settings.constants import VALID_EFFORT_VALUES


class TestAnthropic:
    def test_opus_5_carries_the_full_ladder(self):
        """Wire-probed 2026-07-25: opus-5 accepts xhigh and max."""
        assert effort_options("anthropic", "claude-opus-5") == {
            "supported": True,
            "levels": ["low", "medium", "high", "xhigh", "max"],
        }

    def test_sonnet_4_6_drops_xhigh_but_keeps_max(self):
        """sonnet-4-6 rejects xhigh with a 400 whose message names the levels
        it does take — "high, low, max, medium" — so max stays and only xhigh
        is gated. Offering xhigh here would 400 every subsequent request."""
        r = effort_options("anthropic", "claude-sonnet-4-6")

        assert r["supported"] is True
        assert "xhigh" not in r["levels"]
        assert "max" in r["levels"]

    def test_a_model_outside_the_effort_allowlist_gets_no_step(self):
        """Effort on a non-effort Claude model is a silent no-op: the request
        succeeds and the level is dropped on the floor. That is exactly the
        dead choice step 3 must not offer."""
        assert effort_options("anthropic", "claude-haiku-4-5-20251001") == {
            "supported": False,
            "levels": [],
        }


class TestOpenAI:
    def test_a_reasoning_model_drops_max(self):
        """OPENAI_REASONING_EFFORTS omits max — OpenAI 400s on it for the same
        model OpenRouter tolerates it for, so the clamp is provider-scoped."""
        r = effort_options("openai", "gpt-5.6-luna")

        assert r["supported"] is True
        assert "max" not in r["levels"]
        assert r["levels"] == ["low", "medium", "high", "xhigh"]

    def test_none_never_leaks_into_the_ladder(self):
        """``none`` is an OpenAI level but not a clawcodex one — _do_set_effort
        would reject it, so an offered row would be unapplicable."""
        assert "none" not in effort_options("openai", "gpt-5.6-luna")["levels"]

    def test_a_non_reasoning_model_gets_no_step(self):
        """A reasoning block on gpt-4o is a hard 400, verified live."""
        assert effort_options("openai", "gpt-4o")["supported"] is False

    def test_chat_variants_are_not_reasoning_models(self):
        assert effort_options("openai", "gpt-5-chat-latest")["supported"] is False


class TestOtherProviders:
    @pytest.mark.parametrize(
        ("provider", "model"),
        [("deepseek", "deepseek-v4-flash"), ("moonshot", "kimi-k3"), ("zai", "glm-5.2")],
    )
    def test_providers_without_a_table_get_the_full_ladder(self, provider, model):
        """No per-model effort table exists for these, and the compat paths
        pass the value through as a body field unsupported models IGNORE
        (kimi-k3 was probed doing exactly that). Withholding the ladder would
        remove a working control from every non-first-party provider."""
        assert effort_options(provider, model) == {"supported": True, "levels": list(LADDER)}

    def test_an_alias_resolves_to_its_canonical_provider(self):
        """``glm`` is an alias of ``zai``; a raw compare would misroute it."""
        assert effort_options("glm", "glm-5.2") == effort_options("zai", "glm-5.2")

    def test_an_unknown_provider_still_answers(self):
        assert effort_options("not-a-real-provider", "some-model")["supported"] is True


class TestLadderContract:
    def test_the_ladder_matches_the_settings_source_of_truth(self):
        """Every offered level has to survive _do_set_effort, which validates
        against VALID_EFFORT_VALUES. A drift here means a row the picker shows
        and the backend refuses."""
        assert LADDER == tuple(v for v in VALID_EFFORT_VALUES if v)

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("anthropic", "claude-opus-5"),
            ("anthropic", "claude-sonnet-4-6"),
            ("openai", "gpt-5.6-luna"),
            ("deepseek", "deepseek-v4-flash"),
        ],
    )
    def test_no_offered_level_is_unapplicable(self, provider, model):
        for level in effort_options(provider, model)["levels"]:
            assert level in VALID_EFFORT_VALUES

    def test_auto_is_never_a_backend_level(self):
        """The picker prepends `auto` itself — it means "clear the override",
        not a value to send."""
        assert "auto" not in effort_options("anthropic", "claude-opus-5")["levels"]

    def test_an_unsupported_model_reports_an_empty_ladder(self):
        """`supported: False` and a non-empty `levels` would let a caller that
        checks only one of the two offer dead rows."""
        r = effort_options("anthropic", "claude-haiku-4-5-20251001")

        assert r["supported"] is False and r["levels"] == []

    def test_a_missing_model_is_not_an_error(self):
        assert effort_options("anthropic", None)["supported"] is False
