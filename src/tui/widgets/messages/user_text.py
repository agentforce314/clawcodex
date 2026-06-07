"""Facade — tui/widgets/messages/user_text.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "UserTextMessage",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.widgets.messages.user_text as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
