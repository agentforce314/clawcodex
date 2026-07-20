"""Memory write-origin provenance — ContextVar distinguishing autonomous
background-review writes from foreground user-directed writes.

Port of ``reference_projects/hermes-agent/tools/skill_provenance.py``
(memory-only in this port — clawcodex has no ``skill_manage`` write engine
yet). The self-improvement review fork binds ``"background_review"`` on its
worker thread before running; every tool call it dispatches inherits the
value through the thread's context (``asyncio.run`` copies the current
context into its tasks). Foreground turns keep the default.

Downstream readers:
* the write-approval gate records the origin on staged pending records
  (audit: *who* asked for this write);
* future curation (hermes doc 06) may only ever manage artifacts created
  under the background origin — user-directed writes belong to the user.

Usage::

    token = set_current_write_origin(BACKGROUND_REVIEW)
    try:
        ...  # review fork runs here
    finally:
        reset_current_write_origin(token)

    # inside a tool / gate:
    if is_background_review():
        ...
"""

from __future__ import annotations

import contextvars

_write_origin: contextvars.ContextVar[str] = contextvars.ContextVar(
    "memory_write_origin",
    default="foreground",
)

#: Sentinel origin value bound by the self-improvement review fork.
BACKGROUND_REVIEW = "background_review"


def set_current_write_origin(origin: str) -> contextvars.Token[str]:
    """Bind the active write origin. Returns a Token for the paired
    :func:`reset_current_write_origin` in a ``finally`` block."""
    return _write_origin.set(origin or "foreground")


def reset_current_write_origin(token: contextvars.Token[str]) -> None:
    """Restore the prior write origin."""
    _write_origin.reset(token)


def get_current_write_origin() -> str:
    """The active write origin: ``"foreground"`` (default — any regular
    agent turn, CLI, subagent) or ``"background_review"`` (the
    self-improvement fork)."""
    return _write_origin.get()


def is_background_review() -> bool:
    """True iff the current write origin is the background review fork."""
    return get_current_write_origin() == BACKGROUND_REVIEW


__all__ = [
    "BACKGROUND_REVIEW",
    "get_current_write_origin",
    "is_background_review",
    "reset_current_write_origin",
    "set_current_write_origin",
]
