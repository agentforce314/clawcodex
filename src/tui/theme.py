"""Facade — tui/theme.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "Palette",
    "list_theme_names",
    "resolve_auto_theme",
    "get_palette",
    "textual_css_overrides",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.theme as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
