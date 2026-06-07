"""Facade — command_system/builtins.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "clear_command_call",
    "help_command_call",
    "skills_command_call",
    "exit_command_call",
    "cron_list_command_call",
    "cron_delete_command_call",
    "cost_command_call",
    "context_command_call",
    "advisor_command_call",
    "compact_command_call",
    "execute_command_sync",
    "get_builtin_commands",
    "register_builtin_commands",
    "execute_command_async",
]


def __getattr__(name: str):
    import clawcodex_ext.command_system.builtins as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
