"""Facade — tui/frame_metrics.py has been moved to clawcodex_ext (lazy proxy).
"""
from __future__ import annotations

__all__ = [
    "FrameEvent",
    "is_enabled",
    "register_frame_observer",
    "emit_frame_event",
    "clear_observers_for_tests",
    "TimedPhase",
]


def __getattr__(name: str):
    import clawcodex_ext.tui.frame_metrics as _mod
    if name in _mod.__dict__:
        val = _mod.__dict__[name]
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
