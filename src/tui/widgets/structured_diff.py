"""Facade — tui/widgets/structured_diff.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "DiffLine",
    "parse_unified_diff",
    "parse_structured_patch",
    "count_changes",
    "render_diff",
    "StructuredDiff",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.widgets.structured_diff as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
