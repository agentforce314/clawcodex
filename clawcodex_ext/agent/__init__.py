"""Downstream agent extensions — background runner and state management."""

from clawcodex_ext.agent.background_runner import (
    launch_background_runner,
    get_background_runner_status,
)
from clawcodex_ext.agent.background_state import (
    background_signal,
    is_backgrounded,
    set_backgrounded,
    signal_background,
    reset_background,
)

__all__ = [
    "launch_background_runner",
    "get_background_runner_status",
    "background_signal",
    "is_backgrounded",
    "set_backgrounded",
    "signal_background",
    "reset_background",
]
