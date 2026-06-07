"""Facade — tui/messages.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "AgentRunStarted",
    "AssistantChunk",
    "ThinkingChunk",
    "AssistantMessage",
    "ToolEventMessage",
    "AdvisorEventMessage",
    "AgentRunFinished",
    "PermissionRequested",
    "PermissionResolved",
    "AskUserQuestionRequested",
    "AskUserQuestionResolved",
    "StateChanged",
    "CancelRequested",
    "PermissionModeCycleRequested",
    "PromptPasted",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.messages as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
