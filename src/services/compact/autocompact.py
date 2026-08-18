"""
Layer 5: Autocompact — full LLM summarization (last resort).

Port of ``typescript/src/services/compact/autoCompact.ts``.

Determines when automatic compaction should trigger based on token usage
and context window size, then delegates to ``compact_conversation()``.
Includes a circuit breaker to prevent infinite retry loops.

PR 2: Cost-aware compaction trigger (mode="cost_aware") replaces pure
token-threshold trigger with break-even analysis:
  effectiveCostPerToken = cacheHitRate * cacheReadPrice + (1-cacheHitRate) * inputPrice
  savingsPerTurn = tokensToCompact * effectiveCostPerToken
  compactionCost = summaryTokens * summaryModelOutputPrice
  turnsToBreakEven = compactionCost / savingsPerTurn
  Only compact if turnsToBreakEven <= break_even_turns (default 10)
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ...types.messages import Message
from ...providers.base import BaseProvider
from ...services.pricing import get_pricing, compute_cost
from ...bootstrap.state import get_model_usage

from .compact import (
    CompactContext,
    CompactionResult,
    compact_conversation,
    _get_recent_cache_hit_rate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (mirroring TypeScript autoCompact.ts)
# ---------------------------------------------------------------------------

# Reserve this many tokens for output during compaction.
# Based on p99.99 of compact summary output being 17,387 tokens.
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

# Auto-compact earlier so long interactive sessions do not accumulate enough
# history to make each successive turn progressively slower (openclaude
# #1949).  Keep the effective-window floor at the old value: coupling the
# floor to this larger threshold buffer would unnecessarily shrink small
# context windows.
AUTOCOMPACT_BUFFER_TOKENS = 30_000
AUTOCOMPACT_FLOOR_BUFFER_TOKENS = 13_000

# Buffer for token warning states (UI warnings)
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000

# Buffer for blocking limit (hard cap)
MANUAL_COMPACT_BUFFER_TOKENS = 3_000

# Maximum consecutive compaction failures before circuit breaker trips.
# BQ 2026-03-10: 1,279 sessions had 50+ consecutive failures (up to 3,272)
# in a single session, wasting ~250K API calls/day globally.
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3

# Minimum input tokens before autocompact can trigger (legacy fallback)
MIN_INPUT_TOKENS_FOR_AUTOCOMPACT = 10_000

# Estimated summary output tokens (p99.99 = 17,387, we use 20k as buffer)
ESTIMATED_SUMMARY_OUTPUT_TOKENS = 20_000

# Default fraction of context that gets compacted (used for token estimation)
DEFAULT_COMPACTION_FRACTION = 0.3


@dataclass
class AutoCompactTracking:
    """Tracks autocompact state across query iterations."""
    consecutive_failures: int = 0
    last_failure_time: float | None = None
    last_compact_time: float | None = None
    total_compactions: int = 0
    compacted: bool = False
    turn_counter: int = 0


def _get_env_int(name: str) -> int | None:
    """Read an integer from an env var, returning None if unset or invalid."""
    val = os.environ.get(name)
    if val is None:
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def _get_env_float(name: str) -> float | None:
    """Read a float from an env var, returning None if unset or invalid."""
    val = os.environ.get(name)
    if val is None:
        return None
    try:
        parsed = float(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def _is_env_truthy(name: str) -> bool:
    """Check if an env var is set to a truthy value."""
    val = os.environ.get(name, "").lower()
    return val in ("1", "true", "yes")


def _estimate_compaction_token_savings(
    input_token_count: int,
    context_window: int,
) -> int:
    """
    Estimate how many tokens would be shed by compaction.

    Uses a conservative fraction of the input tokens (default 30%),
    capped at the context window size.
    """
    # Conservative estimate: compaction typically sheds 20-40% of tokens
    # Use the lower bound for safety
    estimated_shed = int(input_token_count * DEFAULT_COMPACTION_FRACTION)
    return min(estimated_shed, input_token_count)


def _get_cost_aware_compaction_params(
    model: str,
    input_token_count: int,
    context_window: int,
) -> tuple[float, float, float, float] | None:
    """
    Get pricing parameters for cost-aware compaction analysis.

    Returns (input_price, cache_read_price, summary_output_price, cache_hit_rate)
    or None if pricing is unavailable.
    """
    # Get cache hit rate from recent usage
    cache_hit_rate_pct = _get_recent_cache_hit_rate(model)
    if cache_hit_rate_pct is None:
        # No cache data available, assume 0% hit rate (conservative)
        cache_hit_rate = 0.0
    else:
        cache_hit_rate = cache_hit_rate_pct / 100.0

    # Get pricing for the main model (for input/cache_read rates)
    pricing = get_pricing(model)
    if pricing is None:
        return None

    input_price = pricing.get("input", 0)
    cache_read_price = pricing.get("cache_read", 0)

    # If cache_read rate is not explicitly set, it's often a fraction of input rate
    if cache_read_price == 0:
        cache_read_price = input_price * 0.1  # typical 90% discount

    # Get pricing for the summary model (output price)
    # The summary model is typically the same as the main model, but could be different
    # For now, use the main model's output price as a reasonable estimate
    summary_output_price = pricing.get("output", 0)
    if summary_output_price == 0:
        summary_output_price = input_price * 3  # typical 3x ratio

    return input_price, cache_read_price, summary_output_price, cache_hit_rate


def _should_auto_compact_cost_aware(
    input_token_count: int,
    context_window: int,
    model: str,
    *,
    max_output_tokens: int | None = None,
    tracking: AutoCompactTracking | None = None,
    break_even_turns: int = 10,
) -> bool:
    """
    Determine whether autocompact should trigger using cost-aware analysis.

    This implements the break-even analysis from PR 2:
    - effectiveCostPerToken = cacheHitRate * cacheReadPrice + (1-cacheHitRate) * inputPrice
    - savingsPerTurn = tokensToCompact * effectiveCostPerToken
    - compactionCost = summaryTokens * summaryModelOutputPrice
    - turnsToBreakEven = compactionCost / savingsPerTurn
    - Only compact if turnsToBreakEven <= break_even_turns
    """
    if not is_auto_compact_enabled():
        return False

    if input_token_count < MIN_INPUT_TOKENS_FOR_AUTOCOMPACT:
        return False

    # Circuit breaker
    if tracking is not None:
        if tracking.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logger.info(
                "Autocompact circuit breaker active (%d consecutive failures)",
                tracking.consecutive_failures,
            )
            return False

    # Get pricing parameters
    params = _get_cost_aware_compaction_params(model, input_token_count, context_window)
    if params is None:
        # No pricing available, fall back to token threshold
        logger.debug("Cost-aware compaction: no pricing for %s, falling back to token threshold", model)
        threshold = get_auto_compact_threshold(context_window, max_output_tokens)
        return input_token_count >= threshold

    input_price, cache_read_price, summary_output_price, cache_hit_rate = params

    # Calculate effective cost per token (what we pay per token on average)
    effective_cost_per_token = (
        cache_hit_rate * cache_read_price + (1 - cache_hit_rate) * input_price
    )

    # Estimate tokens that would be shed by compaction
    tokens_to_compact = _estimate_compaction_token_savings(input_token_count, context_window)
    if tokens_to_compact <= 0:
        return False

    # Savings per turn = tokens shed * effective cost per token
    savings_per_turn = tokens_to_compact * effective_cost_per_token

    if savings_per_turn <= 0:
        return False

    # Compaction cost = summary output tokens * summary model output price
    # Use ESTIMATED_SUMMARY_OUTPUT_TOKENS as a conservative upper bound
    compaction_cost = ESTIMATED_SUMMARY_OUTPUT_TOKENS * summary_output_price

    # Turns to break even
    turns_to_break_even = compaction_cost / savings_per_turn

    logger.debug(
        "cost-aware compaction: model=%s input_tokens=%d cache_hit_rate=%.2f%% "
        "effective_cost=$%.6f/M tokens_to_compact=%d savings_per_turn=$%.6f "
        "compaction_cost=$%.6f turns_to_break_even=%.1f break_even_turns=%d",
        model,
        input_token_count,
        cache_hit_rate * 100,
        effective_cost_per_token * 1_000_000,
        tokens_to_compact,
        savings_per_turn,
        compaction_cost,
        turns_to_break_even,
        break_even_turns,
    )

    # Only compact if we break even within the configured number of turns
    should_compact = turns_to_break_even <= break_even_turns

    if should_compact:
        logger.info(
            "Cost-aware compaction triggered: model=%s turns_to_break_even=%.1f "
            "(threshold=%d) tokens_shed_est=%d cache_hit=%.1f%%",
            model,
            turns_to_break_even,
            break_even_turns,
            tokens_to_compact,
            cache_hit_rate * 100,
        )
    else:
        logger.debug(
            "Cost-aware compaction deferred: model=%s turns_to_break_even=%.1f > %d",
            model,
            turns_to_break_even,
            break_even_turns,
        )

    return should_compact


def get_effective_context_window_size(
    context_window: int,
    max_output_tokens: int | None = None,
) -> int:
    """
    Returns the context window size minus the max output tokens for the model.

    Port of ``getEffectiveContextWindowSize`` in autoCompact.ts.
    """
    reserved = min(
        max_output_tokens or MAX_OUTPUT_TOKENS_FOR_SUMMARY,
        MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    )

    # Allow override via env var
    auto_compact_window = _get_env_int("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if auto_compact_window is not None:
        context_window = min(context_window, auto_compact_window)

    effective = context_window - reserved

    # Floor: effective context must be at least the summary reservation plus a
    # usable buffer. If it goes lower, the auto-compact threshold becomes
    # negative and fires on every message.
    return max(effective, reserved + AUTOCOMPACT_FLOOR_BUFFER_TOKENS)


def get_auto_compact_threshold(
    context_window: int,
    max_output_tokens: int | None = None,
) -> int:
    """
    Compute the token threshold at which autocompact triggers.

    Port of ``getAutoCompactThreshold`` in autoCompact.ts.
    """
    effective = get_effective_context_window_size(context_window, max_output_tokens)
    # Ramp the buffer from 13k to 30k for mid-sized windows.  A direct jump
    # would make warning thresholds negative and make a one-token increase in
    # context size trigger compaction earlier rather than later.
    buffer = min(
        AUTOCOMPACT_BUFFER_TOKENS,
        max(
            AUTOCOMPACT_FLOOR_BUFFER_TOKENS,
            effective - AUTOCOMPACT_BUFFER_TOKENS,
        ),
    )
    threshold = effective - buffer

    # Override for easier testing of autocompact
    env_pct = _get_env_float("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    if env_pct is not None and 0 < env_pct <= 100:
        pct_threshold = int(effective * (env_pct / 100))
        return min(pct_threshold, threshold)

    return threshold


def is_auto_compact_enabled() -> bool:
    """
    Check whether autocompact is enabled.

    Port of ``isAutoCompactEnabled`` in autoCompact.ts.
    """
    if _is_env_truthy("DISABLE_COMPACT"):
        return False
    if _is_env_truthy("DISABLE_AUTO_COMPACT"):
        return False
    return True


def calculate_token_warning_state(
    token_usage: int,
    context_window: int,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    """
    Calculate the token usage warning state for UI display.

    Port of ``calculateTokenWarningState`` in autoCompact.ts.

    Returns dict with keys:
        percent_left, is_above_warning_threshold, is_above_error_threshold,
        is_above_auto_compact_threshold, is_at_blocking_limit
    """
    auto_compact_threshold = get_auto_compact_threshold(context_window, max_output_tokens)
    effective = get_effective_context_window_size(context_window, max_output_tokens)

    threshold = auto_compact_threshold if is_auto_compact_enabled() else effective

    # Display percentage uses the RAW context window (current TS
    # autoCompact.ts: rawContextWindow feeds percentLeft) — the previous
    # threshold-based denominator was a stale port (C3a review F3).
    # floor(x+0.5) = JS Math.round half-up; Python round() banks.
    percent_left = (
        max(
            0,
            math.floor(
                ((context_window - token_usage) / context_window) * 100 + 0.5
            ),
        )
        if context_window > 0
        else 0
    )

    warning_threshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    error_threshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS

    is_above_warning = token_usage >= warning_threshold
    is_above_error = token_usage >= error_threshold
    is_above_auto_compact = (
        is_auto_compact_enabled() and token_usage >= auto_compact_threshold
    )

    # Blocking limit
    default_blocking_limit = effective - MANUAL_COMPACT_BUFFER_TOKENS
    blocking_override = _get_env_int("CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE")
    blocking_limit = blocking_override if blocking_override is not None else default_blocking_limit

    return {
        "percent_left": percent_left,
        "is_above_warning_threshold": is_above_warning,
        "is_above_error_threshold": is_above_error,
        "is_above_auto_compact_threshold": is_above_auto_compact,
        "is_at_blocking_limit": token_usage >= blocking_limit,
    }


def should_auto_compact(
    input_token_count: int,
    context_window: int,
    *,
    max_output_tokens: int | None = None,
    tracking: AutoCompactTracking | None = None,
    threshold_fraction: float | None = None,
    mode: str = "token_threshold",
    break_even_turns: int = 10,
    model: str | None = None,
) -> bool:
    """
    Determine whether autocompact should trigger.

    Uses the TS-aligned threshold calculation by default (mode="token_threshold").
    When mode="cost_aware", uses break-even analysis based on cache hit rate and pricing.
    ``threshold_fraction`` is accepted for backward compatibility but
    ignored when the TS-aligned calculation is available.
    """
    if not is_auto_compact_enabled():
        return False

    if input_token_count < MIN_INPUT_TOKENS_FOR_AUTOCOMPACT:
        return False

    # Circuit breaker
    if tracking is not None:
        if tracking.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logger.info(
                "Autocompact circuit breaker active (%d consecutive failures)",
                tracking.consecutive_failures,
            )
            return False

    if mode == "cost_aware":
        if model is None:
            logger.warning("Cost-aware compaction requires model parameter, falling back to token threshold")
        else:
            return _should_auto_compact_cost_aware(
                input_token_count,
                context_window,
                model,
                max_output_tokens=max_output_tokens,
                tracking=tracking,
                break_even_turns=break_even_turns,
            )

    # Legacy token-threshold mode
    threshold = get_auto_compact_threshold(context_window, max_output_tokens)

    logger.debug(
        "autocompact: tokens=%d threshold=%d effective=%d",
        input_token_count,
        threshold,
        get_effective_context_window_size(context_window, max_output_tokens),
    )

    return input_token_count >= threshold


async def auto_compact_if_needed(
    messages: list[Message],
    input_token_count: int,
    context_window: int,
    provider: BaseProvider,
    model: str,
    *,
    max_output_tokens: int | None = None,
    threshold_fraction: float | None = None,
    tracking: AutoCompactTracking | None = None,
    custom_instructions: str | None = None,
    read_file_state: dict[str, Any] | None = None,
    plan_file_path: str | None = None,
    memory_paths: set[str] | None = None,
    compaction_mode: str = "token_threshold",
    break_even_turns: int = 10,
) -> CompactionResult | None:
    """
    Trigger autocompact if token thresholds are exceeded.

    Args:
        read_file_state, plan_file_path, memory_paths: forwarded to the
            ``CompactContext`` so post-compact attachments (file restore,
            plan restore) fire from auto-compact, not just `/compact`.
        compaction_mode: "token_threshold" (legacy) or "cost_aware" (PR 2)
        break_even_turns: number of turns to break even (default 10, used in cost_aware mode)

    Returns:
        ``CompactionResult`` if compaction was performed, else ``None``.
    """
    if _is_env_truthy("DISABLE_COMPACT"):
        return None

    if not should_auto_compact(
        input_token_count, context_window,
        max_output_tokens=max_output_tokens,
        threshold_fraction=threshold_fraction,
        tracking=tracking,
        mode=compaction_mode,
        break_even_turns=break_even_turns,
        model=model,
    ):
        return None

    logger.info(
        "Autocompact triggered: %d input tokens (mode=%s, context_window=%d)",
        input_token_count,
        compaction_mode,
        context_window,
    )

    ctx = CompactContext(
        provider=provider,
        model=model,
        messages=messages,
        custom_instructions=custom_instructions,
        trigger="auto",
        read_file_state=read_file_state,
        plan_file_path=plan_file_path,
        memory_paths=memory_paths,
    )

    try:
        result = await compact_conversation(ctx)

        if tracking is not None:
            tracking.consecutive_failures = 0
            tracking.last_compact_time = time.time()
            tracking.total_compactions += 1
            tracking.compacted = True

        logger.info(
            "Autocompact completed: %d tokens saved",
            result.tokens_saved,
        )
        return result

    except Exception as e:
        logger.warning("Autocompact failed: %s", e)
        if tracking is not None:
            tracking.consecutive_failures += 1
            tracking.last_failure_time = time.time()
        return None
