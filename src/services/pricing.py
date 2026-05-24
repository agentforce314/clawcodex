"""Per-model pricing for cost calculation.

Pure functions and constants — no state, no class. The actual accumulation
of cost state lives in ``src.bootstrap.state`` (via
``add_to_total_cost_state`` and friends); this module just computes the
dollar cost of a usage record.

Extracted from the (deprecated) ``src/services/cost_tracker.py`` as part
of Phase 2.3 of the ch03 state refactor.
"""

from __future__ import annotations

from typing import Any


PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-20250514": {
        "input": 15.0 / 1_000_000,
        "output": 75.0 / 1_000_000,
        "cache_creation": 18.75 / 1_000_000,
        "cache_read": 1.50 / 1_000_000,
    },
    "claude-sonnet-4-20250514": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_creation": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
    },
    "claude-3-7-sonnet-20250219": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_creation": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
    },
    "claude-3-5-sonnet-20241022": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_creation": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
    },
    "claude-3-5-sonnet-20240620": {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_creation": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
    },
    "claude-3-haiku-20240307": {
        "input": 0.25 / 1_000_000,
        "output": 1.25 / 1_000_000,
        "cache_creation": 0.30 / 1_000_000,
        "cache_read": 0.03 / 1_000_000,
    },
    "claude-3-5-haiku-20241022": {
        "input": 1.0 / 1_000_000,
        "output": 5.0 / 1_000_000,
        "cache_creation": 1.25 / 1_000_000,
        "cache_read": 0.10 / 1_000_000,
    },
}

DEFAULT_PRICING: dict[str, float] = {
    "input": 3.0 / 1_000_000,
    "output": 15.0 / 1_000_000,
    "cache_creation": 3.75 / 1_000_000,
    "cache_read": 0.30 / 1_000_000,
}


def get_pricing(model: str) -> dict[str, float]:
    """Return per-token prices for ``model``. Falls back to the closest
    prefix match, then to ``DEFAULT_PRICING``."""
    if model in PRICING:
        return PRICING[model]
    for prefix, pricing in PRICING.items():
        if model.startswith(prefix.rsplit("-", 1)[0]):
            return pricing
    return DEFAULT_PRICING


def compute_cost(model: str, usage: dict[str, Any]) -> float:
    """Compute USD cost for a usage record. Pure function."""
    pricing = get_pricing(model)
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    cache_creation = int(usage.get("cache_creation_input_tokens", 0))
    cache_read = int(usage.get("cache_read_input_tokens", 0))
    return (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_creation * pricing["cache_creation"]
        + cache_read * pricing["cache_read"]
    )


__all__ = ["PRICING", "DEFAULT_PRICING", "get_pricing", "compute_cost"]
