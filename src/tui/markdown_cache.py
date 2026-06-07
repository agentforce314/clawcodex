"""Facade — tui/markdown_cache.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "has_markdown_syntax",
    "MarkdownCache",
    "get_markdown_cache",
    "reset_markdown_cache_for_tests",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.markdown_cache as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
