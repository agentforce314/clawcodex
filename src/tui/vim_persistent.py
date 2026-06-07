"""Facade — tui/vim_persistent.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "InsertChange",
    "OperatorChange",
    "OperatorTextObjChange",
    "OperatorFindChange",
    "ReplaceChange",
    "XChange",
    "ToggleCaseChange",
    "IndentChange",
    "OpenLineChange",
    "JoinChange",
    "PersistentState",
    "replay",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.vim_persistent as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
