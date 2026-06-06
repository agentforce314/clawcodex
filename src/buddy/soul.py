"""Deterministic soul generator.

``NAME_PREFIXES`` / ``NAME_SUFFIXES`` / ``PERSONALITIES`` are split out of
the TS buddy command. ``PET_REACTIONS`` lives in
``src/command_system/buddy_command.py`` because it's live (re-seeded on
every pet via ``time.time()``) rather than persistent.
"""
from __future__ import annotations

import time
from typing import Sequence

from src.buddy.types import StoredCompanion


NAME_PREFIXES: tuple[str, ...] = (
    'Byte', 'Echo', 'Glint', 'Miso', 'Nova',
    'Pixel', 'Rune', 'Static', 'Vector', 'Whisk',
)

NAME_SUFFIXES: tuple[str, ...] = (
    'bean', 'bit', 'bud', 'dot', 'ling',
    'loop', 'moss', 'patch', 'puff', 'spark',
)

PERSONALITIES: tuple[str, ...] = (
    'Curious and quietly encouraging',
    'A patient little watcher with strong debugging instincts',
    'Playful, observant, and suspicious of flaky tests',
    'Calm under pressure and fond of clean diffs',
    'A tiny terminal gremlin who likes successful builds',
)


def _fnv1a_32(s: str) -> int:
    """FNV-1a 32-bit hash."""
    h = 2166136261
    for c in s:
        h ^= ord(c)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _pick_deterministic(items: Sequence[str], seed: str) -> str:
    return items[_fnv1a_32(seed) % len(items)]


def create_stored_companion(user_id: str) -> StoredCompanion:
    """Create a fresh ``StoredCompanion`` with deterministic name and personality."""
    prefix = _pick_deterministic(NAME_PREFIXES, f"{user_id}:prefix")
    suffix = _pick_deterministic(NAME_SUFFIXES, f"{user_id}:suffix")
    personality = _pick_deterministic(PERSONALITIES, f"{user_id}:personality")
    return {
        'name': f"{prefix}{suffix}",
        'personality': f"{personality}.",
        'hatched_at': int(time.time() * 1000),
    }


__all__ = [
    'NAME_PREFIXES', 'NAME_SUFFIXES', 'PERSONALITIES',
    'create_stored_companion',
]
