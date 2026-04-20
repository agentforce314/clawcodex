"""Token budget — mirrors TypeScript query/tokenBudget.ts and utils/tokenBudget.ts.

Provides budget checking (continue/stop decisions), diminishing returns detection,
and budget parsing from user messages.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

COMPLETION_THRESHOLD = 0.9
DIMINISHING_THRESHOLD = 500

MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}

SHORTHAND_START_RE = re.compile(r"^\s*\+(\d+(?:\.\d+)?)\s*(k|m|b)\b", re.IGNORECASE)
SHORTHAND_END_RE = re.compile(r"\s\+(\d+(?:\.\d+)?)\s*(k|m|b)\s*[.!?]?\s*$", re.IGNORECASE)
VERBOSE_RE = re.compile(r"\b(?:use|spend)\s+(\d+(?:\.\d+)?)\s*(k|m|b)\s*tokens?\b", re.IGNORECASE)


@dataclass
class BudgetTracker:
    continuation_count: int = 0
    last_delta_tokens: int = 0
    last_global_turn_tokens: int = 0
    started_at: float = field(default_factory=time.time)


def create_budget_tracker() -> BudgetTracker:
    return BudgetTracker()


@dataclass(frozen=True)
class ContinueDecision:
    action: Literal["continue"] = "continue"
    nudge_message: str = ""
    continuation_count: int = 0
    pct: int = 0
    turn_tokens: int = 0
    budget: int = 0


@dataclass(frozen=True)
class StopDecision:
    action: Literal["stop"] = "stop"
    completion_event: dict | None = None


TokenBudgetDecision = ContinueDecision | StopDecision


def check_token_budget(
    tracker: BudgetTracker,
    agent_id: str | None,
    budget: int | None,
    global_turn_tokens: int,
) -> TokenBudgetDecision:
    if agent_id or budget is None or budget <= 0:
        return StopDecision(completion_event=None)

    turn_tokens = global_turn_tokens
    pct = round((turn_tokens / budget) * 100)
    delta_since_last = global_turn_tokens - tracker.last_global_turn_tokens

    is_diminishing = (
        tracker.continuation_count >= 3
        and delta_since_last < DIMINISHING_THRESHOLD
        and tracker.last_delta_tokens < DIMINISHING_THRESHOLD
    )

    if not is_diminishing and turn_tokens < budget * COMPLETION_THRESHOLD:
        tracker.continuation_count += 1
        tracker.last_delta_tokens = delta_since_last
        tracker.last_global_turn_tokens = global_turn_tokens
        nudge = get_budget_continuation_message(pct, turn_tokens, budget)
        return ContinueDecision(
            nudge_message=nudge,
            continuation_count=tracker.continuation_count,
            pct=pct,
            turn_tokens=turn_tokens,
            budget=budget,
        )

    if is_diminishing or tracker.continuation_count > 0:
        return StopDecision(
            completion_event={
                "continuation_count": tracker.continuation_count,
                "pct": pct,
                "turn_tokens": turn_tokens,
                "budget": budget,
                "diminishing_returns": is_diminishing,
                "duration_ms": int((time.time() - tracker.started_at) * 1000),
            },
        )

    return StopDecision(completion_event=None)


def get_budget_continuation_message(pct: int, turn_tokens: int, budget: int) -> str:
    return (
        f"Stopped at {pct}% of token target ({turn_tokens:,} / {budget:,}). "
        f"Keep working \u2014 do not summarize."
    )


def _parse_budget_match(value: str, suffix: str) -> int:
    return int(float(value) * MULTIPLIERS[suffix.lower()])


def parse_token_budget(text: str) -> int | None:
    start_match = SHORTHAND_START_RE.search(text)
    if start_match:
        return _parse_budget_match(start_match.group(1), start_match.group(2))

    end_match = SHORTHAND_END_RE.search(text)
    if end_match:
        return _parse_budget_match(end_match.group(1), end_match.group(2))

    verbose_match = VERBOSE_RE.search(text)
    if verbose_match:
        return _parse_budget_match(verbose_match.group(1), verbose_match.group(2))

    return None


@dataclass(frozen=True)
class BudgetPosition:
    start: int
    end: int


def find_token_budget_positions(text: str) -> list[BudgetPosition]:
    positions: list[BudgetPosition] = []

    start_match = SHORTHAND_START_RE.search(text)
    if start_match:
        offset = start_match.start() + len(start_match.group(0)) - len(start_match.group(0).lstrip())
        positions.append(BudgetPosition(start=offset, end=start_match.end()))

    end_match = SHORTHAND_END_RE.search(text)
    if end_match:
        end_start = end_match.start() + 1
        already_covered = any(end_start >= p.start and end_start < p.end for p in positions)
        if not already_covered:
            positions.append(BudgetPosition(start=end_start, end=end_match.end()))

    for match in VERBOSE_RE.finditer(text):
        positions.append(BudgetPosition(start=match.start(), end=match.end()))

    return positions
