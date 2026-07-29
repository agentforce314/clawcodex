"""The three user-facing permission levels behind ``/permissions``.

Single source of truth, mirrored by ``ui-tui/src/lib/permissionLevels.ts`` (the
two are pinned equal by ``tests/test_permission_levels.py``) and consumed by the
Python ``/permissions`` command (``src/command_system/permissions_command.py``).

Each level is a *label* over an existing engine :class:`PermissionMode` — the
permission engine itself is untouched. ``plan`` and ``dontAsk`` are deliberately
absent: they are workflow / strict-deny modes, not points on a looseness ladder.
They stay reachable via ``/plan``, ``--permission-mode``, and the power-user form
``/permissions <raw-mode>``.

The descriptions are written against what the engine actually does. They are NOT
copied from the picker this was modeled on (OpenAI Codex), whose wording
describes internet gating clawcodex does not implement and a classifier that
here lives only on the flag-gated ``auto`` mode. Sources for each claim:

* ``ask`` → ``default``: read-only Bash auto-allows
  (:mod:`src.permissions.bash_mode_validation`), out-of-root reads ask
  (:func:`src.permissions.filesystem`), out-of-root Bash writes ask
  (``src/tool_system/tools/bash/bash_tool.py``).
* ``approve`` → ``acceptEdits``: edits inside the working roots auto-allow
  (``check.py`` acceptEdits branch) plus a *small fixed list* of write and
  read-only commands (:mod:`src.permissions.bash_mode_validation`). ``git
  commit``, ``npm install``, ``pytest`` and friends all still ask — hence "a
  small set of safe shell commands" rather than "only unsafe things".
* ``full`` → ``bypassPermissions``: the mode short-circuit in ``check.py``.
  Deny/ask *rules* still run first, and ``ExitPlanMode``'s interaction ask is
  bypass-immune — hence the "except where you have configured…" clause.

Level keys are chosen NOT to collide with any :data:`PermissionMode` name, so
``/permissions <arg>`` can accept a level key or a raw mode unambiguously. In
particular the middle level is ``approve``, not ``auto`` — ``auto`` is a real
(internal, classifier-backed) mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import PermissionMode


@dataclass(frozen=True)
class PermissionLevel:
    """One row of the ``/permissions`` picker."""

    #: Stable identifier used by ``/permissions <key>`` and the TUI overlay.
    key: str
    #: Title-case label shown in the picker.
    label: str
    #: The engine mode this level selects.
    mode: PermissionMode
    #: One-line explanation of what the level actually permits.
    description: str


#: Ordered loosest-last, matching the reference picker's 1/2/3 ordering.
PERMISSION_LEVELS: tuple[PermissionLevel, ...] = (
    PermissionLevel(
        key="ask",
        label="Ask for approval",
        mode="default",
        description=(
            "Read files and run read-only commands freely. Editing files, "
            "running other commands, or touching anything outside this "
            "workspace asks first."
        ),
    ),
    PermissionLevel(
        key="approve",
        label="Approve for me",
        mode="acceptEdits",
        description=(
            "Auto-accept file edits in this workspace and a small set of safe "
            "shell commands. Everything else still asks."
        ),
    ),
    PermissionLevel(
        key="full",
        label="Full Access",
        mode="bypassPermissions",
        description=(
            "Edit any file and run any command without asking, except where "
            "you have configured a deny or ask rule. Exercise caution when "
            "using."
        ),
    ),
)

PERMISSION_LEVEL_KEYS: tuple[str, ...] = tuple(lv.key for lv in PERMISSION_LEVELS)

#: The mode every level maps to — used to decide whether a mode is on the ladder.
PERMISSION_LEVEL_MODES: tuple[PermissionMode, ...] = tuple(lv.mode for lv in PERMISSION_LEVELS)


def level_for_key(key: str) -> PermissionLevel | None:
    """Look up a level by its ``key`` (case-insensitive). ``None`` if unknown."""
    wanted = key.strip().lower()
    for level in PERMISSION_LEVELS:
        if level.key == wanted:
            return level
    return None


def level_for_mode(mode: str | None) -> PermissionLevel | None:
    """Return the level a mode belongs to, or ``None`` for off-ladder modes.

    ``plan`` / ``dontAsk`` / ``auto`` / ``bubble`` all return ``None`` — they are
    real modes with no row in the picker, and callers must render them as
    "no level selected" rather than silently showing one of the three.
    """
    for level in PERMISSION_LEVELS:
        if level.mode == mode:
            return level
    return None


def mode_for_key(key: str) -> PermissionMode | None:
    """``/permissions <key>`` → engine mode. ``None`` if ``key`` is not a level."""
    level = level_for_key(key)
    return level.mode if level is not None else None
