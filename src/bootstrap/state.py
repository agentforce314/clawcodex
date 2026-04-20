"""Process-wide session state mirroring ``typescript/src/bootstrap/state.ts``.

Only the small slice of state needed to gate tool availability is modeled for
now: whether the current process is running an interactive REPL/TUI session or
a headless/SDK-style invocation. The default is ``isInteractive = False`` which
matches the TypeScript default and is flipped to ``True`` by ``start_repl`` and
``run_tui`` when they boot an interactive UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _BootstrapState:
    is_interactive: bool = False
    client_type: str = "claude-code"
    extra: dict[str, Any] | None = None


_STATE = _BootstrapState()


def get_is_interactive() -> bool:
    return _STATE.is_interactive


def set_is_interactive(value: bool) -> None:
    _STATE.is_interactive = bool(value)


def get_is_non_interactive_session() -> bool:
    return not _STATE.is_interactive


def get_client_type() -> str:
    return _STATE.client_type


def set_client_type(value: str) -> None:
    _STATE.client_type = str(value)


__all__ = [
    "get_is_interactive",
    "set_is_interactive",
    "get_is_non_interactive_session",
    "get_client_type",
    "set_client_type",
]
