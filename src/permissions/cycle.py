"""Facade — permissions/cycle.py has been moved to clawcodex_ext (lazy proxy).

Uses module-level __getattr__ to defer the ext import until
the symbol is actually accessed at runtime, avoiding circular
import chains when parent __init__.py files eagerly import
submodules during package initialization.
"""
from __future__ import annotations

__all__ = [
    "register_cycle_step",
    "get_next_permission_mode",
    "cycle_permission_mode",
]


def __getattr__(name: str):
    """Lazy proxy — import from clawcodex_ext.permissions.cycle on first access."""
    import clawcodex_ext.permissions.cycle as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val  # cache for subsequent access
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

