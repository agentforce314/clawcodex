from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..types.messages import Message
from ..tool_system.context import ToolContext


ContinueReason = Literal[
    "next_turn",
    "max_output_tokens_recovery",
    "max_output_tokens_escalate",
    "reactive_compact_retry",
    "collapse_drain_retry",
    "stop_hook_blocking",
    "token_budget_continuation",
]


@dataclass(frozen=True)
class Transition:
    reason: ContinueReason
    attempt: int | None = None
    committed: int | None = None


@dataclass(frozen=True)
class Terminal:
    reason: str
    error: Exception | None = None
    turn_count: int | None = None


ToolUseContext = ToolContext


@dataclass
class QueryState:
    messages: list[Message]
    tool_use_context: ToolUseContext
    auto_compact_tracking: Any | None = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    stop_hook_active: bool | None = None
    turn_count: int = 1
    transition: Transition | None = None
