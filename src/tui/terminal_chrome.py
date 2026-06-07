"""Facade — tui/terminal_chrome.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "set_terminal_title",
    "clear_terminal_title",
    "set_tab_status",
    "ring_bell",
    "notify_iterm2",
    "notify_kitty",
    "notify_ghostty",
    "set_progress",
    "enable_focus_events",
    "disable_focus_events",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.terminal_chrome as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
