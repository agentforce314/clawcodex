"""Priority-based routing for input-owning overlays.

Mirrors ``getFocusedInputDialog`` in ``typescript/src/screens/REPL.tsx``.
Widgets and screens call :func:`should_show_overlay` / :func:`owns_input`
to decide whether they should accept keystrokes on a given frame.

The router itself is stateless; it reads :class:`src.tui.state.AppState`
and returns the winner so screens can push / pop modal screens in a
consistent order.
"""

from __future__ import annotations

from .state import AppState, FocusedDialog, priority_of


def owns_input(state: AppState, dialog: FocusedDialog) -> bool:
    """Return ``True`` if ``dialog`` currently has the input focus."""

    return state.focused_dialog == dialog


def should_show_overlay(state: AppState, dialog: FocusedDialog) -> bool:
    """Return ``True`` if the given overlay should be rendered.

    An overlay is shown either when it currently owns input OR when its
    priority is higher than the prompt but lower than whatever owns
    input. In Phase 1 we model the simpler "own input" semantics —
    additional overlays like the cost-threshold warning that can be
    visible without owning input will land in Phase 2.
    """

    if dialog == state.focused_dialog:
        return True
    return False


def higher_priority(a: FocusedDialog, b: FocusedDialog) -> FocusedDialog:
    return a if priority_of(a) >= priority_of(b) else b


__all__ = ["owns_input", "should_show_overlay", "higher_priority"]
