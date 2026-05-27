"""Soul generator: deterministic name + personality + hatched_at."""
from __future__ import annotations

import re

from src.buddy.soul import (
    NAME_PREFIXES,
    NAME_SUFFIXES,
    PERSONALITIES,
    create_stored_companion,
)


def test_create_stored_companion_deterministic_except_hatched_at() -> None:
    """Same user_id → same name + personality; hatched_at varies."""
    a = create_stored_companion('alice')
    b = create_stored_companion('alice')
    assert a['name'] == b['name']
    assert a['personality'] == b['personality']
    # hatched_at can equal if calls happen in same millisecond, but both
    # are still positive ints.
    assert a['hatched_at'] >= 0
    assert b['hatched_at'] >= 0


def test_different_users_get_different_souls() -> None:
    """alice vs bob → at least one of name/personality differs."""
    a = create_stored_companion('alice')
    b = create_stored_companion('bob')
    assert a['name'] != b['name'] or a['personality'] != b['personality']


def test_name_format() -> None:
    """Name is `<Prefix><suffix>` where prefix is title-cased and
    suffix is lowercase."""
    c = create_stored_companion('test')
    name = c['name']
    # Find which prefix and suffix were chosen
    matched_prefix = next(
        (p for p in NAME_PREFIXES if name.startswith(p)), None,
    )
    assert matched_prefix is not None, f"name {name!r} starts with no prefix"
    rest = name[len(matched_prefix):]
    assert rest in NAME_SUFFIXES, f"name suffix {rest!r} not in NAME_SUFFIXES"


def test_personality_ends_with_period() -> None:
    """Personality is `<base>.` per TS buddy.tsx:74."""
    c = create_stored_companion('test')
    assert c['personality'].endswith('.')
    # The base (without the trailing dot) should be one of the
    # PERSONALITIES entries.
    base = c['personality'][:-1]
    assert base in PERSONALITIES


def test_hatched_at_is_unix_millis() -> None:
    """hatched_at is a positive int in milliseconds (TS contract)."""
    import time
    before = int(time.time() * 1000)
    c = create_stored_companion('x')
    after = int(time.time() * 1000)
    assert before <= c['hatched_at'] <= after
