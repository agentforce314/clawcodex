"""Per-model pricing for cost calculation.

Pure functions and constants — no state, no class. The actual accumulation
of cost state lives in ``src.bootstrap.state`` (via
``add_to_total_cost_state`` and friends); this module just computes the
dollar cost of a usage record.

Pricing mirrors ``typescript/src/utils/modelCost.ts``: published Anthropic
list prices per million tokens for first-party direct calls. Proxies
(litellm, openrouter, bedrock, vertex) may apply different rates;
``compute_cost`` reports the upstream model price regardless of how the
request is actually routed. Users running through a proxy should treat
the displayed cost as a directional estimate.

For non-Anthropic models we fall back to ``DEFAULT_PRICING`` (sonnet-tier
rates) — accurate per-provider tables are a future followup; for now the
default gives the right order-of-magnitude.
"""

from __future__ import annotations

from typing import Any


# Pricing tiers (USD per million tokens) — mirrors TS modelCost.ts.
_TIER_3_15 = {
    "input": 3.0 / 1_000_000,
    "output": 15.0 / 1_000_000,
    "cache_creation": 3.75 / 1_000_000,
    "cache_read": 0.30 / 1_000_000,
}
_TIER_15_75 = {
    "input": 15.0 / 1_000_000,
    "output": 75.0 / 1_000_000,
    "cache_creation": 18.75 / 1_000_000,
    "cache_read": 1.50 / 1_000_000,
}
_TIER_5_25 = {
    "input": 5.0 / 1_000_000,
    "output": 25.0 / 1_000_000,
    "cache_creation": 6.25 / 1_000_000,
    "cache_read": 0.50 / 1_000_000,
}
_TIER_10_50 = {
    "input": 10.0 / 1_000_000,
    "output": 50.0 / 1_000_000,
    "cache_creation": 12.50 / 1_000_000,
    "cache_read": 1.00 / 1_000_000,
}
_TIER_HAIKU_45 = {
    "input": 1.0 / 1_000_000,
    "output": 5.0 / 1_000_000,
    "cache_creation": 1.25 / 1_000_000,
    "cache_read": 0.10 / 1_000_000,
}
_TIER_HAIKU_35 = {
    "input": 0.80 / 1_000_000,
    "output": 4.0 / 1_000_000,
    "cache_creation": 1.0 / 1_000_000,
    "cache_read": 0.08 / 1_000_000,
}
_TIER_HAIKU_3 = {
    "input": 0.25 / 1_000_000,
    "output": 1.25 / 1_000_000,
    "cache_creation": 0.30 / 1_000_000,
    "cache_read": 0.03 / 1_000_000,
}
# DeepSeek V4 (USD per million tokens). DeepSeek's automatic prefix cache
# bills cache HITS at the low ``cache_read`` rate and cache MISSES at the
# normal input rate; there is no separate cache-write charge, so
# ``cache_creation`` mirrors ``input`` (a non-cached token is just input).
# DeepSeekProvider maps its usage onto the Anthropic convention
# (``input_tokens`` = miss, ``cache_read_input_tokens`` = hit), so these tiers
# price correctly through the generic ``compute_cost``.
_TIER_DEEPSEEK_FLASH = {
    "input": 0.14 / 1_000_000,
    "output": 0.28 / 1_000_000,
    "cache_creation": 0.14 / 1_000_000,
    "cache_read": 0.0028 / 1_000_000,
}
_TIER_DEEPSEEK_PRO = {
    "input": 0.435 / 1_000_000,
    "output": 0.87 / 1_000_000,
    "cache_creation": 0.435 / 1_000_000,
    "cache_read": 0.003625 / 1_000_000,
}
# MiniMax M3 pay-as-you-go rates in USD per million tokens. Prompt size is the
# complete request input, including cache creation and cache read tokens.
_MINIMAX_M3_INPUT_TIER_LIMIT = 512_000
_TIER_MINIMAX_M3_STANDARD = {
    "input": 0.30 / 1_000_000,
    "output": 1.20 / 1_000_000,
    "cache_creation": 0.30 / 1_000_000,
    "cache_read": 0.06 / 1_000_000,
}
_TIER_MINIMAX_M3_STANDARD_LONG = {
    "input": 0.60 / 1_000_000,
    "output": 2.40 / 1_000_000,
    "cache_creation": 0.60 / 1_000_000,
    "cache_read": 0.12 / 1_000_000,
}
_TIER_MINIMAX_M3_PRIORITY = {
    "input": 0.45 / 1_000_000,
    "output": 1.80 / 1_000_000,
    "cache_creation": 0.45 / 1_000_000,
    "cache_read": 0.09 / 1_000_000,
}
_TIER_MINIMAX_M3_PRIORITY_LONG = {
    "input": 0.90 / 1_000_000,
    "output": 3.60 / 1_000_000,
    "cache_creation": 0.90 / 1_000_000,
    "cache_read": 0.18 / 1_000_000,
}
_TIER_MINIMAX_M27 = {
    "input": 0.30 / 1_000_000,
    "output": 1.20 / 1_000_000,
    "cache_creation": 0.375 / 1_000_000,
    "cache_read": 0.06 / 1_000_000,
}
# Meta Muse Spark 1.1 (api.meta.ai, OpenAI-compatible). Meta's published rates:
# $1.25/M input, $4.25/M output, $0.15/M cached input. OpenAI-style caching has
# no separate cache-write charge, so ``cache_creation`` mirrors ``input``.
# NOTE: the generic OpenAI-compat usage builder does not (yet) map
# ``prompt_tokens_details.cached_tokens`` onto ``cache_read_input_tokens`` —
# only the hand-written DeepSeek provider does — so today ``cache_read`` is
# inert for Meta: cached input is billed at the full input rate in the cost
# display, an over-estimate on the cached portion ($1.25 vs $0.15/M, ~8x).
# The displayed cost is thus a safe upper bound, consistent with the other
# registry providers; wiring the mapping into ``_build_usage_dict`` is a
# separate change (it would affect all OpenAI-compat providers). The real
# cache-read rate is recorded here for when that lands.
_TIER_MUSE_SPARK = {
    "input": 1.25 / 1_000_000,
    "output": 4.25 / 1_000_000,
    "cache_creation": 1.25 / 1_000_000,
    "cache_read": 0.15 / 1_000_000,
}


# Exact-match table — keyed by canonical model name. Order DOESN'T matter
# for exact match but DOES matter for the prefix fallback below
# (more-specific keys must come first). See ``get_pricing``.
PRICING: dict[str, dict[str, float]] = {
    # Haiku family
    "claude-haiku-4-5": _TIER_HAIKU_45,
    "claude-3-5-haiku-20241022": _TIER_HAIKU_45,
    "claude-3-haiku-20240307": _TIER_HAIKU_3,
    # Sonnet family — all on the standard 3/15 tier
    "claude-sonnet-4-6": _TIER_3_15,
    "claude-sonnet-4-5": _TIER_3_15,
    "claude-sonnet-4-20250514": _TIER_3_15,
    "claude-3-7-sonnet-20250219": _TIER_3_15,
    "claude-3-5-sonnet-20241022": _TIER_3_15,
    "claude-3-5-sonnet-20240620": _TIER_3_15,
    # Fable family — frontier tier above Opus (10/50)
    "claude-fable-5": _TIER_10_50,
    # Opus family — 4.5+ on the 5/25 tier, 4.0/4.1 on 15/75
    "claude-opus-4-8": _TIER_5_25,
    "claude-opus-4-7": _TIER_5_25,
    "claude-opus-4-6": _TIER_5_25,
    "claude-opus-4-5": _TIER_5_25,
    "claude-opus-4-1": _TIER_15_75,
    "claude-opus-4-20250514": _TIER_15_75,
    # DeepSeek V4 (api.deepseek.com). OpenRouter's ``deepseek/…`` ids resolve
    # here too via get_pricing's vendor-prefix strip — consistent with how
    # every proxied model is priced at its upstream rate.
    "deepseek-v4-flash": _TIER_DEEPSEEK_FLASH,
    "deepseek-v4-pro": _TIER_DEEPSEEK_PRO,
    "MiniMax-M3": _TIER_MINIMAX_M3_STANDARD,
    "MiniMax-M2.7": _TIER_MINIMAX_M27,
    # Meta Muse Spark (api.meta.ai)
    "muse-spark-1.1": _TIER_MUSE_SPARK,
}


# Legacy alias — older callers (cost_tracker facade) still default to
# the sonnet-3/15 tier when a model is missing. The status-bar path
# uses ``get_pricing`` which returns None for unknowns and skips the
# segment rather than silently mispricing (see critic C1 below).
DEFAULT_PRICING: dict[str, float] = _TIER_3_15


# Family prefixes for fallback when an exact match misses. Order
# matters: longer/more-specific *canonical-form* prefixes first within
# a family. Critic C2: the bare ``claude-opus-4`` prefix was REMOVED
# — it forced future variants (claude-opus-4-9-future) onto the legacy
# 15/75 tier, but the modern Opus trend (4-5/4-6/4-7) is on the 5/25
# tier. Unknown opus-4.x now falls through to None instead of being
# tagged with the wrong price.
_FAMILY_PREFIXES: list[tuple[str, dict[str, float]]] = [
    ("claude-fable-5", _TIER_10_50),
    ("claude-haiku-4", _TIER_HAIKU_45),
    ("claude-3-5-haiku", _TIER_HAIKU_45),
    ("claude-3-haiku", _TIER_HAIKU_3),
    ("claude-opus-4-8", _TIER_5_25),
    ("claude-opus-4-7", _TIER_5_25),
    ("claude-opus-4-6", _TIER_5_25),
    ("claude-opus-4-5", _TIER_5_25),
    ("claude-opus-4-1", _TIER_15_75),
    ("claude-sonnet-4", _TIER_3_15),
    ("claude-3-7-sonnet", _TIER_3_15),
    ("claude-3-5-sonnet", _TIER_3_15),
]


def _get_exact_pricing(
    model: str,
    *,
    input_tokens: int,
    service_tier: str,
) -> dict[str, float] | None:
    if model != "MiniMax-M3":
        return PRICING.get(model)

    is_long_context = input_tokens > _MINIMAX_M3_INPUT_TIER_LIMIT
    if service_tier == "priority":
        return (
            _TIER_MINIMAX_M3_PRIORITY_LONG
            if is_long_context
            else _TIER_MINIMAX_M3_PRIORITY
        )
    return (
        _TIER_MINIMAX_M3_STANDARD_LONG
        if is_long_context
        else _TIER_MINIMAX_M3_STANDARD
    )


def get_pricing(
    model: str,
    *,
    input_tokens: int = 0,
    service_tier: str = "standard",
) -> dict[str, float] | None:
    """Return per-token prices for ``model``, or ``None`` if unknown.

    ``input_tokens`` is the complete prompt size for one request. MiniMax M3
    uses it with ``service_tier`` to select its standard/priority and
    short/long-context rate.

    Lookup order:
      1. Exact match in ``PRICING``.
      2. Strip a leading ``<vendor>/`` segment (openrouter convention,
         e.g. ``anthropic/claude-opus-4.1``) and retry exact match.
      3. Family-prefix match against ``_FAMILY_PREFIXES``.
      4. ``None`` — caller decides whether to suppress the cost
         display (status-bar path) or fall back to ``DEFAULT_PRICING``
         (legacy cost-tracker facade).

    Critic C1: returning ``None`` instead of a generic Sonnet-tier
    fallback prevents silently mispricing unknown non-Claude models by
    3-10× (e.g. Gemini, GPT-5 vs sonnet $3/$15). The user picks "no
    number" over "wrong number" for status-bar honesty. DeepSeek V4 is
    now tabled above; other per-provider tiers remain a future PR.
    """
    if not model:
        return None
    if model in PRICING:
        return _get_exact_pricing(
            model,
            input_tokens=input_tokens,
            service_tier=service_tier,
        )
    if "/" in model:
        bare = model.split("/", 1)[1]
        if bare in PRICING:
            return _get_exact_pricing(
                bare,
                input_tokens=input_tokens,
                service_tier=service_tier,
            )
        for prefix, pricing in _FAMILY_PREFIXES:
            if bare.startswith(prefix):
                return pricing
    for prefix, pricing in _FAMILY_PREFIXES:
        if model.startswith(prefix):
            return pricing
    return None


def is_known_pricing(model: str) -> bool:
    """True iff ``model`` has a real pricing entry (not the legacy
    default fallback). Callers that need a "show or hide?" flag for
    cost UI can use this instead of inspecting the return shape."""
    return get_pricing(model) is not None


def compute_cost(model: str, usage: dict[str, Any]) -> float:
    """Compute USD cost for a usage record. Pure function.

    Returns 0.0 when the model has no pricing entry (rather than
    guessing with ``DEFAULT_PRICING``). The legacy cost-tracker facade
    that wants the old "always charge something" behavior should call
    ``get_pricing(model) or DEFAULT_PRICING`` explicitly.

    Reads ``input_tokens``, ``output_tokens``,
    ``cache_creation_input_tokens``, and ``cache_read_input_tokens``
    from ``usage``. Missing keys default to zero so callers that only
    track input+output still get a sensible result.
    """
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    prompt_tokens = input_tokens + cache_creation + cache_read
    pricing = get_pricing(
        model,
        input_tokens=prompt_tokens,
        service_tier=str(usage.get("service_tier") or "standard"),
    )
    if pricing is None:
        return 0.0
    return (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_creation * pricing["cache_creation"]
        + cache_read * pricing["cache_read"]
    )


def format_cost_usd(amount: float) -> str:
    """Render a USD amount compactly for the status bar.

    * ``< $0.01`` → 4 decimals (``$0.0034``) so sub-cent activity is
      visible.
    * ``< $10``   → 3 decimals (``$1.234``) — typical session range,
      cents-accurate.
    * ``≥ $10``   → 2 decimals (``$12.34``) — penny-rounding is fine
      when the order of magnitude is high.

    Always shows ``$0.0000`` (not blank) for non-positive amounts so the
    caller can omit the segment with a single zero-check rather than
    parsing the format.
    """
    if amount <= 0:
        return "$0.0000"
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 10:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


def compute_session_cost(
    *,
    worker_model: str | None,
    worker_input_tokens: int,
    worker_output_tokens: int,
    worker_cache_creation_tokens: int = 0,
    worker_cache_read_tokens: int = 0,
    advisor_model: str | None = None,
    advisor_input_tokens: int = 0,
    advisor_output_tokens: int = 0,
) -> tuple[float, float, float]:
    """Compute (worker_cost, advisor_cost, total_cost) for a session.

    Caller passes the running token accumulators from whichever surface
    is rendering. The function does the per-model
    pricing lookups and returns the three dollar amounts so the caller
    can format/display however it wants.

    Worker cache token counts default to zero — callers that don't
    track cache separately (most of them, today) just get input+output
    pricing. Advisor cache is ignored entirely; the advisor's separate
    API call doesn't get cache hits across runs.
    """
    worker_cost = 0.0
    if worker_model and (worker_input_tokens or worker_output_tokens):
        worker_cost = compute_cost(worker_model, {
            "input_tokens": worker_input_tokens,
            "output_tokens": worker_output_tokens,
            "cache_creation_input_tokens": worker_cache_creation_tokens,
            "cache_read_input_tokens": worker_cache_read_tokens,
        })
    advisor_cost = 0.0
    if advisor_model and (advisor_input_tokens or advisor_output_tokens):
        advisor_cost = compute_cost(advisor_model, {
            "input_tokens": advisor_input_tokens,
            "output_tokens": advisor_output_tokens,
        })
    return worker_cost, advisor_cost, worker_cost + advisor_cost


__all__ = [
    "PRICING",
    "DEFAULT_PRICING",
    "get_pricing",
    "is_known_pricing",
    "compute_cost",
    "compute_session_cost",
    "format_cost_usd",
]
