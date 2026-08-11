"""Query 终态枚举、持有器与跨入口结果映射。

属于 ``B3. Query refactor I``；该模块是终态事实源，``transitions``
仅保留兼容导出，避免现有调用方形成第二套终态定义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TerminalReason = Literal[
    "blocking_limit",
    "image_error",
    "model_error",
    "aborted_streaming",
    "prompt_too_long",
    "completed",
    "stop_hook_prevented",
    "aborted_tools",
    "hook_stopped",
    "max_turns",
    "max_cost",
    "tool_failure_loop",
    "empty_response",
]

PYTHON_ONLY_TERMINAL_REASONS: frozenset[str] = frozenset({
    "empty_response",
    "max_cost",
})

EARLY_STOP_SUBTYPES: dict[str, str] = {
    "tool_failure_loop": "error_during_execution",
    "empty_response": "error_during_execution",
    "blocking_limit": "error_during_execution",
    "prompt_too_long": "error_during_execution",
    "image_error": "error_during_execution",
    "max_turns": "error_max_turns",
    "max_cost": "error_during_execution",
}


@dataclass(frozen=True)
class Terminal:
    reason: TerminalReason
    error: Exception | None = None
    turn_count: int | None = None


class TerminalHolder:
    """异步生成器退出时保存唯一 ``Terminal``。"""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: Terminal | None = None


def set_terminal(
    holder: TerminalHolder,
    natural_termination: list[bool],
    terminal: Terminal,
) -> None:
    """原子写入终态并标记自然退出。"""

    holder.value = terminal
    natural_termination[0] = True
