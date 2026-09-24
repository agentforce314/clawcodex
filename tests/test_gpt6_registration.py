"""GPT-6 (Astra / Sol / Luna) registration: context windows, pricing, lists.

Numbers are from developers.openai.com/api/docs/models/gpt-6-{astra,sol,luna}
read 2026-09-24; pinned here so a later edit cannot drift silently.
"""

from __future__ import annotations

import pytest

from src.models.configs import get_model_config
from src.providers import PROVIDER_INFO
from src.providers.openai_responses import supports_reasoning, supports_verbosity
from src.services.pricing import get_pricing

GPT6 = ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna")

# (input, cached input, output) per 1M tokens, list price.
PUBLISHED = {
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
}


@pytest.mark.parametrize("model", GPT6)
def test_listed_for_the_openai_provider(model):
    assert model in PROVIDER_INFO["openai"]["available_models"]


@pytest.mark.parametrize("model", GPT6)
def test_context_window_is_the_smaller_real_input_limit(model):
    """922K max input on the API, 872K on the ChatGPT backend; over-estimating
    makes auto-compact fire past the limit, so the row holds the smaller."""
    config = get_model_config(model)
    assert config is not None and config.model_id == model
    assert config.context_window == 872_000
    assert config.max_output_tokens == 128_000


def test_unlisted_gpt6_variant_does_not_fall_to_the_272k_catch_all():
    assert get_model_config("gpt-6-sol-pro").context_window == 872_000


@pytest.mark.parametrize("model", GPT6)
def test_wire_capabilities(model):
    assert supports_reasoning(model)
    assert supports_verbosity(model)


@pytest.mark.parametrize("model", GPT6)
def test_published_rates_and_the_272k_long_context_tier(model):
    inp, cached, out = PUBLISHED[model]
    short = get_pricing(model, input_tokens=1_000)
    assert short["input"] * 1e6 == pytest.approx(inp)
    assert short["cache_read"] * 1e6 == pytest.approx(cached)
    assert short["output"] * 1e6 == pytest.approx(out)
    # 2x input and cache rates, 1.5x output for the full request above 272K.
    long = get_pricing(model, input_tokens=272_001)
    assert long["input"] == pytest.approx(short["input"] * 2)
    assert long["cache_read"] == pytest.approx(short["cache_read"] * 2)
    assert long["output"] == pytest.approx(short["output"] * 1.5)
    assert get_pricing(model, input_tokens=272_000) == short
