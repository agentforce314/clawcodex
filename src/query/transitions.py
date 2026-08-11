"""Query 继续状态与可变循环状态。

终态定义位于 :mod:`src.query.terminal`；本模块保留旧导入出口以兼容现有
调用方，但不再维护第二套 Terminal 枚举或映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..tool_system.context import ToolContext
from ..types.messages import Message
from .terminal import (
    EARLY_STOP_SUBTYPES,
    PYTHON_ONLY_TERMINAL_REASONS,
    Terminal,
    TerminalHolder,
    TerminalReason,
    set_terminal,
)


ContinueReason = Literal[
    "next_turn",
    "max_output_tokens_recovery",
    "max_output_tokens_escalate",
    "reactive_compact_retry",
    "collapse_drain_retry",
    "stop_hook_blocking",
    "token_budget_continuation",
    "continuation_nudge",
]


@dataclass(frozen=True)
class Transition:
    reason: ContinueReason
    attempt: int | None = None
    committed: int | None = None


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
    # 上一轮异步生成的工具摘要；未来接线前保持状态槽稳定。
    pending_tool_use_summary: Any | None = None
    # 普通 continuation 与空响应 continuation 使用独立上限。
    continuation_nudge_count: int = 0
    empty_turn_nudge_count: int = 0
    transition: Transition | None = None


__all__ = [
    "ContinueReason",
    "EARLY_STOP_SUBTYPES",
    "PYTHON_ONLY_TERMINAL_REASONS",
    "QueryState",
    "Terminal",
    "TerminalHolder",
    "TerminalReason",
    "ToolUseContext",
    "Transition",
    "set_terminal",
]
