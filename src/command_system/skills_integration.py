"""Facade — command_system/skills_integration.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "skill_to_prompt_command",
    "register_skill_as_command",
    "load_and_register_skills",
    "get_skill_command",
    "load_skill_from_directory",
    "execute_skill_command",
]


def __getattr__(name: str):
    import clawcodex_ext.command_system.skills_integration as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
