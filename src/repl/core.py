"""Facade — repl/core.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "ClawcodexREPL",
]


def __getattr__(name: str):
    """Lazy import from clawcodex_ext.repl.core on first access."""
    import clawcodex_ext.repl.core as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
