"""Facade — tui/declared_cursor.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "CursorDeclaration",
    "DeclaredCursor",
    "get_default_declared_cursor",
    "publish_cursor_position",
    "flush_pending",
    "reset_for_tests",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.declared_cursor as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
