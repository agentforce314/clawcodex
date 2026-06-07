"""Facade — tui/commands.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "CommandDispatchResult",
    "CommandSuggestion",
    "build_command_suggestions",
    "build_command_words",
    "dispatch_local_command",
    "dispatch_registry_command",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.commands as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
