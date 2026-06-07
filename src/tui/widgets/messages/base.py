"""Facade — tui/widgets/messages/base.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "BaseRow",
    "SystemMessage",
    "RowHeader",
    "row_container",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.widgets.messages.base as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
