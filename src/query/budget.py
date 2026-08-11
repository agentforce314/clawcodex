"""Runtime budget governance for the canonical Query loop.

The guard is deliberately side-effect free: callers provide the current
usage at each boundary, making model/retry/tool checks deterministic and
straightforward to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable, Literal


BudgetReason = Literal[
    "max_turns",
    "max_cost",
    "max_input_tokens",
    "max_output_tokens",
    "deadline",
]


@dataclass(frozen=True)
class BudgetViolation:
    reason: BudgetReason
    limit: int | float
    actual: int | float


@dataclass(frozen=True)
class BudgetGuard:
    """Unified turns/tokens/cost/time backstop.

    A zero or ``None`` limit means unlimited.  ``deadline`` is an absolute
    monotonic-clock value so retry sleeps and tool execution consume the same
    wall-clock budget as model calls.
    """

    max_turns: int | None = None
    max_cost_usd: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    deadline: float | None = None
    clock: Callable[[], float] = monotonic

    def check(
        self,
        *,
        turn_count: int = 0,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> BudgetViolation | None:
        if self.max_turns and turn_count > self.max_turns:
            return BudgetViolation("max_turns", self.max_turns, turn_count)
        if self.max_cost_usd and cost_usd >= self.max_cost_usd:
            return BudgetViolation("max_cost", self.max_cost_usd, cost_usd)
        if self.max_input_tokens and input_tokens >= self.max_input_tokens:
            return BudgetViolation(
                "max_input_tokens", self.max_input_tokens, input_tokens,
            )
        if self.max_output_tokens and output_tokens >= self.max_output_tokens:
            return BudgetViolation(
                "max_output_tokens", self.max_output_tokens, output_tokens,
            )
        if self.deadline is not None:
            now = self.clock()
            if now >= self.deadline:
                return BudgetViolation("deadline", self.deadline, now)
        return None


def resolve_max_turns(
    explicit: int | None,
    configured: int | None,
    *,
    default: int,
) -> int:
    """Resolve launch override → setting → product default.

    An explicit ``0`` is preserved as unlimited.  A configured ``0`` is the
    schema default (no persisted override), so the surface default remains in
    force; positive configured values become the shared backstop.
    """

    if explicit is not None:
        return explicit
    if configured is not None and configured > 0:
        return configured
    return default
