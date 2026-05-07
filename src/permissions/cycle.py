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


def get_next_permission_mode(context: ToolPermissionContext) -> PermissionMode:
    """Return the next mode when the user presses Shift+Tab.

    Mirrors ``getNextPermissionMode`` in
    ``typescript/src/utils/permissions/getNextPermissionMode.ts:34-79``.

    Cycle:

    - ``default`` → ``acceptEdits``
    - ``acceptEdits`` → ``plan``
    - ``plan`` → ``bypassPermissions`` (when available) else ``default``
    - ``bypassPermissions`` → ``default``
    - ``dontAsk`` / ``auto`` / ``bubble`` → ``default`` (escape hatch — these
      modes are not part of the user-facing cycle but we still need a defined
      transition so Shift+Tab never strands the user.)
    """
    mode = context.mode
    if mode == "default":
        return "acceptEdits"
    if mode == "acceptEdits":
        return "plan"
    if mode == "plan":
        if context.is_bypass_permissions_mode_available:
            return "bypassPermissions"
        return "default"
    if mode == "bypassPermissions":
        return "default"
    # dontAsk, auto, bubble all fall through to default.
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
