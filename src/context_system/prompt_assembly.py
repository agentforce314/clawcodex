"""Facade — context_system/prompt_assembly.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "register_memory_section_builder",
    "clear_context_caches",
    "get_user_context",
    "get_system_context",
    "fetch_system_prompt_parts",
    "append_system_context",
    "append_system_context_blocks",
    "prepend_user_context",
    "get_system_prompt_cache",
    "build_full_system_prompt",
    "build_full_system_prompt_blocks",
]


def __getattr__(name: str):
    """Lazy import from clawcodex_ext.context_system.prompt_assembly on first access."""
    import clawcodex_ext.context_system.prompt_assembly as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
