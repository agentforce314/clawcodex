"""Type/constants invariants."""
from __future__ import annotations

import dataclasses

from src.buddy.types import (
    EYES,
    HATS,
    RARITIES,
    RARITY_COLORS,
    RARITY_STARS,
    RARITY_WEIGHTS,
    SPECIES,
    STAT_NAMES,
)


def test_rarity_keys_match_across_dicts() -> None:
    """RARITY_WEIGHTS / RARITY_STARS / RARITY_COLORS keys == RARITIES."""
    assert set(RARITY_WEIGHTS.keys()) == set(RARITIES)
    assert set(RARITY_STARS.keys()) == set(RARITIES)
    assert set(RARITY_COLORS.keys()) == set(RARITIES)


def test_rarity_colors_values_are_palette_keys() -> None:
    """Every RARITY_COLORS value must be a valid Palette field name."""
    from src.utils.theme import Palette
    palette_fields = {f.name for f in dataclasses.fields(Palette)}
    for rarity, key in RARITY_COLORS.items():
        assert key in palette_fields, (
            f"RARITY_COLORS[{rarity!r}] = {key!r} is not a Palette field"
        )


def test_species_count() -> None:
    assert len(SPECIES) == 18


def test_eyes_count() -> None:
    assert len(EYES) == 6


def test_hats_count_and_includes_none() -> None:
    assert len(HATS) == 8
    assert 'none' in HATS


def test_stat_names_count() -> None:
    assert len(STAT_NAMES) == 5


def test_rarity_weights_sum() -> None:
    """Total weight is 100 (TS contract from types.ts:126-132)."""
    assert sum(RARITY_WEIGHTS.values()) == 100
