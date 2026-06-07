"""Facade — tui/__init__.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "ClawCodexTUI",
    "AdvisorEventMessage",
    "AgentRunFinished",
    "AgentRunStarted",
    "AssistantChunk",
    "AssistantMessage",
    "ToolEventMessage",
]


def __getattr__(name: str):
    import clawcodex_ext.tui as _mod
    val = getattr(_mod, name)
    globals()[name] = val
    return val
