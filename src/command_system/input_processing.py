"""Facade — command_system/input_processing.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "ParsedInput",
    "parse_user_input",
    "expand_at_mentions",
    "format_at_mention_attachments",
    "build_image_content_blocks",
    "expand_agent_mentions",
    "validate_input",
    "InputHistory",
    "is_multiline_trigger",
    "is_multiline_complete",
    "suggest_commands",
]


def __getattr__(name: str):
    import clawcodex_ext.command_system.input_processing as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
