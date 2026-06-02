"""Permission-mode cycling for the Shift+Tab keybinding.

Mirrors ``typescript/src/utils/permissions/getNextPermissionMode.ts``. The TS
reference has an Anthropic-internal ``USER_TYPE === 'ant'`` branch and a
``TRANSCRIPT_CLASSIFIER``-gated ``auto`` cycle target; we omit both — Python
exposes the public cycle only. Once the LLM auto-mode classifier lands in
Python, ``canCycleToAuto`` will get its own implementation here.
"""

from __future__ import annotations

from .types import PermissionMode, ToolPermissionContext
from .updates import apply_permission_update
from .types import PermissionUpdateSetMode


# ---------------------------------------------------------------------------
# Cycle table registry — downstream extensions can inject additional steps.
# The default table matches the upstream cycle.  Extensions call
# ``register_cycle_step()`` to insert transitions (e.g.  bypassPermissions →
# dontAsk) without modifying this file.
# ---------------------------------------------------------------------------

# Each entry is (source_mode, target_mode).  The table is consulted in order;
# first match wins.  The final fallback for any unmatched mode is "default".
_CYCLE_TABLE: list[tuple[str, str]] = [
    ("default", "acceptEdits"),
    ("acceptEdits", "plan"),
    ("plan", "bypassPermissions"),   # guarded by is_bypass_permissions_mode_available
]


def register_cycle_step(source: str, target: str, *, after: str | None = None) -> None:
    """Register an additional cycle transition.

    Args:
        source: The mode to transition *from*.
        target: The mode to transition *to*.
        after: If given, insert after the existing entry whose *source*
            equals this value.  Otherwise append at the end (but before
            the implicit ``→ default`` fallback).

    Example::

        # Insert bypassPermissions → dontAsk after the bypassPermissions row
        register_cycle_step("bypassPermissions", "dontAsk", after="bypassPermissions")
    """
    entry = (source, target)
    if after is not None:
        for idx, (s, _t) in enumerate(_CYCLE_TABLE):
            if s == after:
                _CYCLE_TABLE.insert(idx + 1, entry)
                return
    _CYCLE_TABLE.append(entry)


def get_next_permission_mode(context: ToolPermissionContext) -> PermissionMode:
    """Return the next mode when the user presses Shift+Tab.

    Mirrors ``getNextPermissionMode`` in
    ``typescript/src/utils/permissions/getNextPermissionMode.ts:34-79``.

    Default cycle:

    - ``default`` → ``acceptEdits``
    - ``acceptEdits`` → ``plan``
    - ``plan`` → ``bypassPermissions`` (when available) else ``default``
    - ``bypassPermissions`` → ``default``
    - ``auto`` / ``bubble`` → ``default`` (escape hatch — these
      modes are not part of the user-facing cycle but we still need a defined
      transition so Shift+Tab never strands the user.)

    Downstream extensions can extend the cycle via :func:`register_cycle_step`.
    """
    mode = context.mode
    for source, target in _CYCLE_TABLE:
        if mode != source:
            continue
        # Guard: "plan → bypassPermissions" is only valid when the mode is
        # available.  Fall through to default otherwise.
        if target == "bypassPermissions" and not context.is_bypass_permissions_mode_available:
            return "default"
        return target
    # auto, bubble, and any unrecognised mode fall through to default.
    return "default"


def cycle_permission_mode(
    context: ToolPermissionContext,
) -> tuple[PermissionMode, ToolPermissionContext]:
    """Compute the next mode and return the (mode, updated_context) pair.

    Mirrors ``cyclePermissionMode`` in
    ``typescript/src/utils/permissions/getNextPermissionMode.ts:88-101``.
    The updated context is produced via :func:`apply_permission_update` with
    a ``setMode`` update so any future hooks that observe context updates fire
    consistently.
    """
    next_mode = get_next_permission_mode(context)
    next_context = apply_permission_update(
        context,
        PermissionUpdateSetMode(
            type="setMode",
            destination="session",
            mode=next_mode,
        ),
    )
    return next_mode, next_context
