"""Facade — tui/widgets/select_list.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "SelectOption",
    "SelectList",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.widgets.select_list as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
