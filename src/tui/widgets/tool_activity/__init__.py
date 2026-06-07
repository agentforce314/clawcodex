"""Facade — tui/widgets/tool_activity/__init__.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "build_tool_activity",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.widgets.tool_activity.__init__ as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
