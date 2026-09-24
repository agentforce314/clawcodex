"""The /model picker's step 3: which effort levels a model actually accepts.

The list has to be the levels the model will really take, not the union
ladder ``/effort`` validates against — offering a level the model rejects is
fatal (a 400 on the effort level is retried nowhere), while omitting one
merely hides a choice.
"""

import pytest

from src.providers.effort_options import LADDER, effort_options
from src.settings.constants import VALID_EFFORT_VALUES


@pytest.fixture(autouse=True)
def _no_real_credentials(tmp_path, monkeypatch):
    """OpenAI mode is inferred from stored ChatGPT credentials when a caller
    does not say; keep that inference off the developer's real login."""
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


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
    """API-key mode unless a test says otherwise: without the explicit flag
    the mode is inferred from stored ChatGPT credentials, which would make
    these tests depend on whoever runs them."""

    @staticmethod
    def _api(model):
        return effort_options("openai", model, openai_subscription=False)

    def test_a_reasoning_model_drops_max(self):
        """gpt-5.6-luna 400s on max over the public API (probed 2026-08-01)
        while OpenRouter tolerates it, so the clamp is provider-scoped."""
        r = self._api("gpt-5.6-luna")

        assert r["supported"] is True
        assert r["levels"] == ["low", "medium", "high", "xhigh"]

    def test_gpt6_offers_max_over_the_api(self):
        """developers.openai.com lists max for all three GPT-6 models."""
        for model in ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna"):
            assert self._api(model)["levels"] == ["low", "medium", "high", "xhigh", "max"]

    def test_none_never_leaks_into_the_ladder(self):
        """``none`` is an OpenAI level but not a clawcodex one — _do_set_effort
        would reject it, so an offered row would be unapplicable."""
        assert "none" not in self._api("gpt-5.6-luna")["levels"]
        assert "none" not in self._api("gpt-6-sol")["levels"]

    def test_a_non_reasoning_model_gets_no_step(self):
        """A reasoning block on gpt-4o is a hard 400, verified live."""
        assert self._api("gpt-4o")["supported"] is False

    def test_chat_variants_are_not_reasoning_models(self):
        assert self._api("gpt-5-chat-latest")["supported"] is False


class TestOpenAISubscription:
    """The ChatGPT backend's levels differ per model (probed 2026-09-24)."""

    @pytest.fixture(autouse=True)
    def _no_login(self, monkeypatch):
        from src.providers import openai_subscription_models as catalog

        monkeypatch.setattr(catalog, "load_credentials", lambda: None)

    @staticmethod
    def _sub(model):
        return effort_options("openai", model, openai_subscription=True)

    def test_gpt6_and_gpt56_offer_max(self):
        for model in ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna"):
            assert self._sub(model)["levels"] == ["low", "medium", "high", "xhigh", "max"]

    def test_gpt55_stops_at_xhigh(self):
        assert self._sub("gpt-5.5")["levels"] == ["low", "medium", "high", "xhigh"]

    def test_unknown_older_models_keep_the_conservative_ceiling(self):
        assert self._sub("gpt-5.4")["levels"] == ["low", "medium", "high"]


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
