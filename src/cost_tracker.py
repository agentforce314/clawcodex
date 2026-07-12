"""Cost tracker — facade over ``src.bootstrap.state``.

Phase 2.3 of the ch03 state refactor: the class shape is preserved for
backward compatibility with existing callers (``costHook.py``,
``repl/core.py``, ``tui/app.py``, ``command_system/builtins.py``), but
all state now lives in the bootstrap singleton. Every ``CostTracker``
instance is a view into the same state — there is no "two trackers
disagreeing about cost" problem.

Legacy ``record(label, units)`` API is retained for the existing
``costHook.apply_cost_hook`` callers (which feed the /cost slash
command's event log). The richer ``record_usage(model, usage)`` API
records to the bootstrap singleton's per-model accumulators and
also computes USD cost via ``src.services.pricing``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.bootstrap.state import (
    ModelUsage,
    add_to_total_cost_state,
    cost_state_lock,
    get_model_usage,
    get_total_cost_usd,
)
from src.services.pricing import compute_cost


def record_api_usage(model: str, usage: Any) -> float:
    """Record one API response's usage into the bootstrap singleton.

    The module-level cost-accumulation head (ch04 round-3 G1 — TS
    ``addToTotalSessionCost``, ``claude.ts:2270-2275``): computes USD
    cost from per-model pricing, merges into the per-model accumulator,
    and updates the process-wide totals. Tolerant of ``None``/empty/
    non-mapping usage (records zeros — a streaming completion whose
    ``get_final_message()`` failed yields ``usage={}``).

    Call sites: the query loop's response-complete convergence
    (streaming + watchdog fallback), the compaction summarize calls,
    and the client-side advisor call.
    """
    if not isinstance(usage, dict):
        usage = {}
    cost = 0.0 if usage.get("billing_mode") == "subscription" else compute_cost(model, usage)
    bootstrap_usage = ModelUsage(
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ),
        cache_read_input_tokens=int(
            usage.get("cache_read_input_tokens", 0) or 0
        ),
        cost_usd=cost,
    )
    # ch07 round-4 (critic MAJOR) — hold the accumulator lock across the
    # whole read-modify-write. N parallel subagent threads (Agent is now
    # concurrency-safe; workflow parallel() pre-existing) would otherwise
    # each read the same ``existing``, add their own delta, and clobber one
    # another → lost updates → undercounted cost/tokens. RLock, so the
    # nested add_to_total_cost_state re-acquire is fine.
    with cost_state_lock():
        existing = get_model_usage().get(model)
        if existing is not None:
            bootstrap_usage = ModelUsage(
                input_tokens=existing.input_tokens + bootstrap_usage.input_tokens,
                output_tokens=existing.output_tokens + bootstrap_usage.output_tokens,
                cache_creation_input_tokens=(
                    existing.cache_creation_input_tokens
                    + bootstrap_usage.cache_creation_input_tokens
                ),
                cache_read_input_tokens=(
                    existing.cache_read_input_tokens
                    + bootstrap_usage.cache_read_input_tokens
                ),
                cost_usd=existing.cost_usd + cost,
            )
        add_to_total_cost_state(cost, bootstrap_usage, model)
    return cost


@dataclass
class CostTracker:
    """Facade over the bootstrap singleton.

    Two distinct event streams co-exist for back-compat:

    * ``events`` and ``total_units``: legacy free-form units that the
      ``/cost`` slash command displays. These live on the instance (NOT
      bootstrap) because they're informational — not part of cost
      accounting. Multiple instances accumulate independently.

    * ``record_usage(model, usage)``: routes through the bootstrap
      singleton, so cost USD and per-model breakdown agree across
      every consumer in the process.
    """

    total_units: int = 0
    events: list[str] = field(default_factory=list)
    last_usage: dict[str, Any] | None = None

    def record(self, label: str, units: int) -> None:
        """Legacy event recorder. Used by ``costHook.apply_cost_hook``."""
        self.total_units += units
        self.events.append(f"{label}:{units}")

    def record_usage(self, model: str, usage: dict[str, Any]) -> float:
        """Record a real API usage event into the bootstrap singleton.

        Delegates the durable update to :func:`record_api_usage` and
        additionally stores a ``last_usage`` snapshot for downstream
        consumers (e.g. the ``/cost`` command's last-call summary).
        """
        cost = record_api_usage(model, usage)
        self.last_usage = dict(usage) if isinstance(usage, dict) else {}
        return cost

    @property
    def total_cost_usd(self) -> float:
        """Read-through to the bootstrap singleton — every tracker
        instance sees the same total."""
        return get_total_cost_usd()
