"""
Compact warning suppression state.

Port of ``typescript/src/services/compact/compactWarningState.ts``.

Tracks whether the "context left until autocompact" warning should be
suppressed.  We suppress immediately after successful compaction since
accurate token counts are unavailable until the next API response.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_suppressed: bool = False


def suppress_compact_warning() -> None:
    """Suppress the compact warning.  Call after successful compaction."""
    global _suppressed
    with _lock:
        _suppressed = True


def clear_compact_warning_suppression() -> None:
    """Clear suppression.  Called at the start of a new compact attempt."""
    global _suppressed
    with _lock:
        _suppressed = False


def is_compact_warning_suppressed() -> bool:
    """Return whether the compact warning is currently suppressed."""
    with _lock:
        return _suppressed
