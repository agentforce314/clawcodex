"""Facade — command_system/types.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "CommandType",
    "CommandAvailability",
    "CompactionResult",
    "LocalCommandResult",
    "CommandContext",
    "attach_downstream_context",
    "CommandBase",
    "PromptCommand",
    "LocalCommand",
    "InteractiveCommand",
    "InteractiveOutcome",
    "InteractiveUnavailableError",
    "SkillPromptCommand",
    "UIHost",
    "UIOption",
    "NullUIHost",
    "get_command_name",
    "is_command_enabled",
    "meets_availability_requirement",
]


def __getattr__(name: str):
    import clawcodex_ext.command_system.types as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
