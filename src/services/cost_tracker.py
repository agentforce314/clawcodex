"""Cost tracker — per-model pricing, per-turn + cumulative cost, cache hit savings.

**DEPRECATED**: This module's ``CostTracker`` class is test-only and
predates the Phase 2.3 consolidation onto the bootstrap singleton. New
production code should use:

* ``src.cost_tracker.CostTracker`` — facade over the bootstrap singleton.
* ``src.bootstrap.state.add_to_total_cost_state(...)`` — direct accessor.
* ``src.services.pricing`` — pricing table + ``compute_cost`` pure function.
* ``src.services.cost_restore.restore_cost_state_for_session(...)`` — resume orchestrator.

The class below is retained ONLY because ~23 unit/parity tests still
import it directly. The pricing tables are now re-exported from
``src.services.pricing`` to eliminate the duplication that previously
risked pricing drift between this file and the bootstrap-singleton path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Re-export pricing constants/functions from the canonical source.
# Phase 2.3: this is the single source of truth — both the bootstrap-
# backed facade and this legacy class use these tables.
from src.services.pricing import (
    DEFAULT_PRICING,
    PRICING,
    compute_cost as _compute_cost,
    get_pricing as _get_pricing,
)


@dataclass
class UsageEvent:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ModelUsageEntry:
    """Per-model aggregated usage."""
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    request_count: int = 0


@dataclass
class CostTracker:
    _events: list[UsageEvent] = field(default_factory=list)
    _turn_events: list[UsageEvent] = field(default_factory=list)
    _total_cost: float = 0.0
    _turn_cost: float = 0.0
    _total_input_tokens: int = 0
    _total_output_tokens: int = 0
    _total_cache_creation_tokens: int = 0
    _total_cache_read_tokens: int = 0

    # Duration tracking (R2-WS-9)
    _api_duration_ms: float = 0.0
    _tool_duration_ms: float = 0.0
    _session_start_time: float = field(default_factory=time.time)

    # Lines changed (R2-WS-9)
    _lines_added: int = 0
    _lines_removed: int = 0

    # Web search counting (R2-WS-9)
    _web_search_count: int = 0

    # Per-model aggregation (R2-WS-9)
    _model_usage: dict[str, ModelUsageEntry] = field(default_factory=dict)

    # Unknown model flag (R2-WS-9)
    _unknown_models: set[str] = field(default_factory=set)

    def record_usage(self, model: str, usage: dict[str, Any]) -> float:
        # Legacy cost-tracker contract: always price something (the
        # rolling session total has to be monotonic). For unknown
        # models, fall back to DEFAULT_PRICING — divergent from the
        # status-bar path, which suppresses unknowns entirely.
        pricing = _get_pricing(model) or DEFAULT_PRICING

        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cache_creation = int(usage.get("cache_creation_input_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))

        cost = (
            input_tokens * pricing["input"]
            + output_tokens * pricing["output"]
            + cache_creation * pricing["cache_creation"]
            + cache_read * pricing["cache_read"]
        )

        # Track unknown models
        if model not in PRICING:
            self._unknown_models.add(model)

        event = UsageEvent(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            cost_usd=cost,
        )
        self._events.append(event)
        self._turn_events.append(event)

        self._total_cost += cost
        self._turn_cost += cost
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cache_creation_tokens += cache_creation
        self._total_cache_read_tokens += cache_read

        # Per-model aggregation
        if model not in self._model_usage:
            self._model_usage[model] = ModelUsageEntry(model=model)
        entry = self._model_usage[model]
        entry.input_tokens += input_tokens
        entry.output_tokens += output_tokens
        entry.cache_creation_tokens += cache_creation
        entry.cache_read_tokens += cache_read
        entry.cost_usd += cost
        entry.request_count += 1

        return cost

    def get_total_cost(self) -> float:
        return self._total_cost

    def get_turn_cost(self) -> float:
        return self._turn_cost

    def reset_turn(self) -> None:
        self._turn_events.clear()
        self._turn_cost = 0.0

    def get_total_input_tokens(self) -> int:
        return self._total_input_tokens

    def get_total_output_tokens(self) -> int:
        return self._total_output_tokens

    def get_cache_savings(self) -> float:
        total_savings = 0.0
        for event in self._events:
            pricing = _get_pricing(event.model) or DEFAULT_PRICING
            saved_per_token = pricing["input"] - pricing["cache_read"]
            total_savings += event.cache_read_input_tokens * saved_per_token
        return total_savings

    # --- Duration tracking ---

    def record_api_duration(self, duration_ms: float) -> None:
        """Record API call duration in milliseconds."""
        self._api_duration_ms += duration_ms

    def record_tool_duration(self, duration_ms: float) -> None:
        """Record tool execution duration in milliseconds."""
        self._tool_duration_ms += duration_ms

    def get_api_duration_ms(self) -> float:
        return self._api_duration_ms

    def get_tool_duration_ms(self) -> float:
        return self._tool_duration_ms

    def get_total_session_duration_ms(self) -> float:
        return (time.time() - self._session_start_time) * 1000

    # --- Lines changed ---

    def record_lines_changed(self, added: int = 0, removed: int = 0) -> None:
        """Record lines added/removed by file edits."""
        self._lines_added += added
        self._lines_removed += removed

    def get_lines_added(self) -> int:
        return self._lines_added

    def get_lines_removed(self) -> int:
        return self._lines_removed

    # --- Web search ---

    def record_web_search(self) -> None:
        """Record a web search event."""
        self._web_search_count += 1

    def get_web_search_count(self) -> int:
        return self._web_search_count

    # --- Per-model aggregation ---

    def get_model_usage(self) -> dict[str, ModelUsageEntry]:
        """Get per-model usage aggregation."""
        return dict(self._model_usage)

    def has_unknown_models(self) -> bool:
        """Check if any unknown models were used (pricing may be inaccurate)."""
        return len(self._unknown_models) > 0

    def get_unknown_models(self) -> set[str]:
        return set(self._unknown_models)

    # --- Summary ---

    def get_summary(self) -> dict[str, Any]:
        return {
            "total_cost_usd": self._total_cost,
            "turn_cost_usd": self._turn_cost,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cache_creation_tokens": self._total_cache_creation_tokens,
            "total_cache_read_tokens": self._total_cache_read_tokens,
            "cache_savings_usd": self.get_cache_savings(),
            "event_count": len(self._events),
            "api_duration_ms": self._api_duration_ms,
            "tool_duration_ms": self._tool_duration_ms,
            "session_duration_ms": self.get_total_session_duration_ms(),
            "lines_added": self._lines_added,
            "lines_removed": self._lines_removed,
            "web_search_count": self._web_search_count,
            "models_used": list(self._model_usage.keys()),
            "has_unknown_models": self.has_unknown_models(),
        }

    def is_over_budget(self, max_budget_usd: float | None) -> bool:
        if max_budget_usd is None:
            return False
        return self._total_cost >= max_budget_usd

    def record(self, label: str, units: int) -> None:
        self._total_input_tokens += units
