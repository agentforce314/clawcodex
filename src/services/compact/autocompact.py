"""
Layer 5: Autocompact — full LLM summarization (last resort).

Port of ``typescript/src/services/compact/autoCompact.ts``.

Determines when automatic compaction should trigger based on token usage
and context window size, then delegates to ``compact_conversation()``.
Includes a circuit breaker to prevent infinite retry loops.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ...types.messages import Message
from ...providers.base import BaseProvider

from .compact import (
    CompactContext,
    CompactionResult,
    compact_conversation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (mirroring TypeScript autoCompact.ts)
# ---------------------------------------------------------------------------

# Reserve this many tokens for output during compaction.
# Based on p99.99 of compact summary output being 17,387 tokens.
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

# Buffer between effective context window and autocompact threshold
AUTOCOMPACT_BUFFER_TOKENS = 13_000

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
    return max(effective, reserved + AUTOCOMPACT_BUFFER_TOKENS)


def get_auto_compact_threshold(
    context_window: int,
    max_output_tokens: int | None = None,
) -> int:
    """
    Compute the token threshold at which autocompact triggers.

    Port of ``getAutoCompactThreshold`` in autoCompact.ts.
    """
    effective = get_effective_context_window_size(context_window, max_output_tokens)
    threshold = effective - AUTOCOMPACT_BUFFER_TOKENS

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

    percent_left = max(
        0,
        round(((threshold - token_usage) / threshold) * 100),
    ) if threshold > 0 else 0

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
) -> bool:
    """
    Determine whether autocompact should trigger.

    Uses the TS-aligned threshold calculation by default.
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
) -> CompactionResult | None:
    """
    Trigger autocompact if token thresholds are exceeded.

    Args:
        read_file_state, plan_file_path, memory_paths: forwarded to the
            ``CompactContext`` so post-compact attachments (file restore,
            plan restore) fire from auto-compact, not just `/compact`.

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
    ):
        return None

    logger.info(
        "Autocompact triggered: %d input tokens (threshold=%d, context_window=%d)",
        input_token_count,
        get_auto_compact_threshold(context_window, max_output_tokens),
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
