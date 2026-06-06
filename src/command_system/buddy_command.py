"""``/buddy`` command — hatch / pet / status / mute / unmute / help.

Port of the TypeScript buddy command. ``PET_REACTIONS`` lives here (not in
``src/buddy/soul.py``) because each pet re-seeds from ``time.time()`` —
it's transient reaction text, not persistent soul data.

Help/info argument parsing matches TypeScript via shared lists in
``src/constants/xml.py`` (``COMMON_HELP_ARGS``, ``COMMON_INFO_ARGS``).
Notably ``?`` routes to INFO (status), not HELP.
"""
from __future__ import annotations

import time
from typing import Sequence

from src.buddy.companion import companion_user_id, get_companion
from src.buddy.feature import is_buddy_enabled
from src.buddy.soul import create_stored_companion
from src.command_system.types import (
    CommandContext, LocalCommand, LocalCommandResult,
)
from src.config import _get_default_manager, load_config
from src.constants.xml import COMMON_HELP_ARGS, COMMON_INFO_ARGS


PET_REACTIONS: tuple[str, ...] = (
    'leans into the headpat',
    'does a proud little bounce',
    'emits a content beep',
    'looks delighted',
    'wiggles happily',
)

_HELP_TEXT = (
    "Usage: /buddy [status|mute|unmute]\n\n"
    "Run /buddy with no args to hatch your companion the first time, "
    "then pet it on later runs."
)


def _fnv1a_32(s: str) -> int:
    h = 2166136261
    for c in s:
        h ^= ord(c)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _pick_deterministic(items: Sequence[str], seed: str) -> str:
    return items[_fnv1a_32(seed) % len(items)]


def _title_case(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _save_companion(stored: dict) -> None:
    mgr = _get_default_manager()
    mgr.set_global('companion', stored)
    mgr.set_global('companion_muted', False)


def _set_companion_muted(muted: bool) -> None:
    _get_default_manager().set_global('companion_muted', muted)


def buddy_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """``/buddy`` command body.

    Parsing precedence:
    1. ``help`` / ``-h`` / ``--help`` → help text
    2. ``status`` or any ``COMMON_INFO_ARGS`` entry (incl. ``?``) → status
    3. ``mute`` / ``unmute`` → toggle + confirmation
    4. any other non-empty arg → help text
    5. empty arg + no companion → hatch
    6. empty arg + companion exists → pet
    """
    arg = (args or '').strip().lower()

    if arg in COMMON_HELP_ARGS:
        return LocalCommandResult(type='text', value=_HELP_TEXT)

    if arg in COMMON_INFO_ARGS:
        companion = get_companion()
        if companion is None:
            return LocalCommandResult(
                type='text',
                value='No buddy hatched yet. Run /buddy to hatch one.',
            )
        return LocalCommandResult(
            type='text',
            value=(
                f"{companion.name} is your {_title_case(companion.rarity)} "
                f"{companion.species}. {companion.personality}"
            ),
        )

    if arg in ('mute', 'unmute'):
        muted = arg == 'mute'
        _set_companion_muted(muted)
        return LocalCommandResult(
            type='text',
            value=f"Buddy {'muted' if muted else 'unmuted'}.",
        )

    if arg != '':
        return LocalCommandResult(type='text', value=_HELP_TEXT)

    # Empty arg: hatch (first time) or pet (subsequent).
    companion = get_companion()
    if companion is None:
        user_id = companion_user_id()
        stored = create_stored_companion(user_id)
        _save_companion(stored)
        companion = get_companion()
        if companion is None:
            return LocalCommandResult(
                type='text', value='Failed to hatch companion (state error).',
            )
        return LocalCommandResult(
            type='text',
            value=(
                f"{companion.name} the {companion.species} is now your buddy. "
                f"Run /buddy again to pet them."
            ),
        )

    # Pet path.
    now_ms = int(time.time() * 1000)
    reaction = _pick_deterministic(
        PET_REACTIONS,
        f"{now_ms}:{companion.name}",
    )
    _get_default_manager().set_global('companion_pet_at', now_ms)
    return LocalCommandResult(
        type='text',
        value=f"{companion.name} {reaction}",
    )


BUDDY_COMMAND = LocalCommand(
    name='buddy',
    description='Hatch, pet, and manage your companion',
    argument_hint='[status|mute|unmute|help]',
    immediate=True,
)
BUDDY_COMMAND.set_call(buddy_command_call)


def is_buddy_command_enabled() -> bool:
    """Whether the ``/buddy`` command should be registered."""
    return is_buddy_enabled()


__all__ = [
    'BUDDY_COMMAND',
    'PET_REACTIONS',
    'buddy_command_call',
    'is_buddy_command_enabled',
]
