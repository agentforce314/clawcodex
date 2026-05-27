"""Python port of ``typescript/src/buddy/``.

Headless core ports are present here; the Ink/React widget
(``CompanionSprite``, ``useBuddyNotification``) is deferred per
``my-docs/get-parity-by-folder/buddy-gap-analysis.md`` §3.3.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.buddy.companion import (
    Roll,
    SALT,
    companion_user_id,
    get_companion,
    roll,
    roll_with_seed,
)
from src.buddy.feature import is_buddy_enabled
from src.buddy.notification import (
    find_buddy_trigger_positions,
    is_buddy_live,
    is_buddy_teaser_window,
)
from src.buddy.observer import fire_companion_observer
from src.buddy.prompt import (
    build_companion_intro_attachment,
    companion_intro_text,
    format_companion_intro_attachments,
)
from src.buddy.soul import (
    NAME_PREFIXES,
    NAME_SUFFIXES,
    PERSONALITIES,
    create_stored_companion,
)
from src.buddy.sprites import (
    BODIES,
    HAT_LINES,
    MIN_COLS_FOR_FULL_SPRITE,
    render_face,
    render_sprite,
    sprite_frame_count,
)
from src.buddy.types import (
    EYES,
    HATS,
    RARITIES,
    RARITY_COLORS,
    RARITY_STARS,
    RARITY_WEIGHTS,
    SPECIES,
    STAT_NAMES,
    Companion,
    CompanionBones,
    CompanionSoul,
    Eye,
    Hat,
    Rarity,
    Species,
    StatName,
    StoredCompanion,
)


SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent
    / 'reference_data' / 'subsystems' / 'buddy.json'
)
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = (
    f"Python port of '{ARCHIVE_NAME}' subsystem. Headless core ported; "
    f"Ink/React widget (CompanionSprite, useBuddyNotification) deferred "
    f"to future Textual companion-widget pass — see "
    f"my-docs/get-parity-by-folder/buddy-gap-analysis.md §3.3."
)


# Private helpers (_get_or_create_user_id, _fnv1a_32, _pick_deterministic,
# _mulberry32, _roll_from, etc.) are deliberately NOT re-exported.
__all__ = [
    # snapshot metadata
    'ARCHIVE_NAME', 'MODULE_COUNT', 'PORTING_NOTE', 'SAMPLE_FILES',
    # feature gate
    'is_buddy_enabled',
    # types
    'Companion', 'CompanionBones', 'CompanionSoul', 'Eye', 'Hat',
    'Rarity', 'Species', 'StatName', 'StoredCompanion',
    # data
    'EYES', 'HATS', 'RARITIES', 'RARITY_COLORS', 'RARITY_STARS',
    'RARITY_WEIGHTS', 'SPECIES', 'STAT_NAMES',
    # companion
    'Roll', 'SALT',
    'companion_user_id', 'get_companion', 'roll', 'roll_with_seed',
    # sprites
    'BODIES', 'HAT_LINES', 'MIN_COLS_FOR_FULL_SPRITE',
    'render_face', 'render_sprite', 'sprite_frame_count',
    # prompt
    'build_companion_intro_attachment', 'companion_intro_text',
    'format_companion_intro_attachments',
    # observer
    'fire_companion_observer',
    # notification
    'find_buddy_trigger_positions', 'is_buddy_live',
    'is_buddy_teaser_window',
    # soul
    'NAME_PREFIXES', 'NAME_SUFFIXES', 'PERSONALITIES',
    'create_stored_companion',
]
