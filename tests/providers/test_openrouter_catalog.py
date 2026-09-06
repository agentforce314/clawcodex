"""OpenRouter's curated model catalogue.

The list is a convenience — free-text ids are always accepted — but it is what
``login`` and the /model picker offer, so an id that OpenRouter has delisted
shows up as a row the user can select and then cannot use. That is the same
"offers something you cannot select" shape the picker fix (#772) was about.

These tests deliberately do NOT hit the network: CI would then fail whenever a
vendor retires a model, which is a fact about the world rather than a defect in
the diff. They pin the structural invariants instead. Re-validating the ids
against the live catalogue is a maintenance step:

    curl -s https://openrouter.ai/api/v1/models \\
      | python3 -c "import json,sys; print('\\n'.join(sorted(m['id'] for m in json.load(sys.stdin)['data'])))"
"""

from __future__ import annotations

import pytest

from src.providers import PROVIDER_INFO
from src.providers.openrouter_provider import OpenRouterProvider


CURATED = PROVIDER_INFO["openrouter"]["available_models"]


class TestCuratedCatalogueIsSingleSourced:
    def test_provider_reads_the_registry_rather_than_a_second_copy(self):
        """These were byte-identical duplicates. Two lists for one set drift the
        moment someone updates one, and the halves feed different surfaces —
        the registry drives login + the picker, this drives discovery's
        curation — so drift surfaces as a model one offers and the other drops.
        """
        assert OpenRouterProvider._curated_models() == CURATED

    def test_returns_a_copy_so_a_caller_cannot_mutate_the_registry(self):
        got = OpenRouterProvider._curated_models()
        got.append("mutated/by-caller")

        assert "mutated/by-caller" not in CURATED
        assert OpenRouterProvider._curated_models() == CURATED


class TestCuratedCatalogueShape:
    def test_no_duplicate_ids(self):
        assert len(CURATED) == len(set(CURATED))

    def test_the_default_model_is_one_of_the_offered_ids(self):
        assert PROVIDER_INFO["openrouter"]["default_model"] in CURATED

    @pytest.mark.parametrize("model", CURATED)
    def test_every_id_is_vendor_qualified(self, model):
        """OpenRouter addresses models as ``<vendor>/<model>``; a bare id is a
        copy-paste from another provider's list and 404s at request time."""
        assert model.count("/") == 1, model
        vendor, name = model.split("/")
        assert vendor and name, model
        assert model == model.strip()

    @pytest.mark.parametrize("model", CURATED)
    def test_no_batch_variants(self, model):
        """``:batch`` ids address the asynchronous Batch API, which does not
        answer an interactive agent turn."""
        assert ":batch" not in model, model

    def test_openai_section_leads_with_the_current_frontier(self):
        """Guards against the failure this file was added for: the OpenAI block
        sat on gpt-5/gpt-4o/o1 long after OpenRouter had moved on, and
        ``openai/o1-mini`` had been delisted outright."""
        openai = [m for m in CURATED if m.startswith("openai/")]

        assert openai, "the OpenAI section vanished"
        assert openai[0].startswith("openai/gpt-5.6"), openai[0]
        # Delisted upstream — re-adding it would offer an unusable row.
        assert "openai/o1-mini" not in openai

    def test_the_5_6_tiers_are_ordered_flagship_first(self):
        """Sol / Terra / Luna are capability tiers, not a size ladder, and the
        alphabetical reading puts them in exactly the wrong order — Luna is the
        cheap tier, Sol is the flagship."""
        tiers = [m for m in CURATED if m.startswith("openai/gpt-5.6")]
        rank = {"sol": 0, "terra": 1, "luna": 2}
        seen = [rank[m.split("gpt-5.6-")[1].removesuffix("-pro")] for m in tiers]

        assert seen == sorted(seen), tiers


class TestOpenAIDirectCatalogue:
    """The direct OpenAI provider, which is a separate list from OpenRouter's."""

    DIRECT = PROVIDER_INFO["openai"]["available_models"]

    def test_provider_reads_the_registry_rather_than_a_second_copy(self):
        from src.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="x")

        assert provider.get_available_models() == self.DIRECT
        assert provider.get_available_models() is not self.DIRECT

    def test_leads_with_astra_and_keeps_the_5_6_generation(self):
        assert self.DIRECT[0] == "gpt-6-astra"
        assert {"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} <= set(self.DIRECT)

    def test_ids_are_bare_not_openrouter_qualified(self):
        """``openai/gpt-5.6-sol`` is OpenRouter's addressing; the direct API
        takes the bare id and 404s on the prefixed one."""
        for model in self.DIRECT:
            assert "/" not in model, model


class TestGpt56ModelConfigs:
    @pytest.mark.parametrize(
        "model", ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    )
    def test_carries_the_full_context_window(self, model):
        """Without an entry these fell through to the 272K catch-all, firing
        auto-compact roughly three quarters of a window early."""
        from src.models.configs import get_model_config

        config = get_model_config(model)
        assert config is not None, model
        assert config.context_window == 1_048_576, model

    def test_an_unlisted_5_6_variant_inherits_the_5_6_window(self):
        from src.models.configs import get_model_config

        assert get_model_config("gpt-5.6-sol-pro").context_window == 1_048_576

    def test_an_unknown_gpt_id_still_gets_the_conservative_catch_all(self):
        """The ordering invariant. ``gpt-5.6``'s prefix base is "gpt", so
        placing it above ``gpt-5.5`` would hand every unknown gpt id a 1.05M
        window. Over-estimating overflows the context; under-estimating only
        compacts early, so the catch-all must stay on the 272K entry."""
        from src.models.configs import get_model_config

        assert get_model_config("gpt-9-unreleased").context_window == 272_000
