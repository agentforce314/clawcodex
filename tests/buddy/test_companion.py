"""Companion module: hash, PRNG, roll cache, user_id, get_companion."""
from __future__ import annotations

from typing import Any

import pytest

from src.buddy.companion import (
    SALT,
    _get_or_create_user_id,
    _hash_string,
    _mulberry32,
    _reset_roll_cache_for_tests,
    companion_user_id,
    get_companion,
    roll,
    roll_with_seed,
)
from src.buddy.types import RARITIES


def test_hash_is_deterministic() -> None:
    assert _hash_string('alice') == _hash_string('alice')
    assert _hash_string('alice') != _hash_string('bob')


def test_hash_is_unsigned_32bit() -> None:
    """Output fits in [0, 2^32)."""
    for s in ['', 'a', 'alice', 'a' * 1000]:
        h = _hash_string(s)
        assert 0 <= h < 2**32


def test_mulberry32_is_deterministic() -> None:
    rng1 = _mulberry32(42)
    rng2 = _mulberry32(42)
    for _ in range(10):
        assert rng1() == rng2()


def test_mulberry32_output_in_range() -> None:
    rng = _mulberry32(0xDEADBEEF)
    for _ in range(100):
        x = rng()
        assert 0.0 <= x < 1.0


def test_roll_with_seed_deterministic(isolated_config: dict[str, Any]) -> None:
    """Same seed → same bones (no cache; pure function)."""
    r1 = roll_with_seed('test-seed')
    r2 = roll_with_seed('test-seed')
    assert r1.bones == r2.bones


def test_roll_cache_returns_same_instance(isolated_config: dict[str, Any]) -> None:
    """Cache hit returns the same Roll instance."""
    _reset_roll_cache_for_tests()
    isolated_config['user_id'] = 'alice'
    r1 = roll('alice')
    r2 = roll('alice')
    assert r1 is r2  # cache hit, not just equality


def test_roll_different_user_ids_differ(isolated_config: dict[str, Any]) -> None:
    """Different user_ids → likely different bones (rarity may collide
    but at least one bones field differs in practice)."""
    _reset_roll_cache_for_tests()
    r_alice = roll('alice')
    r_bob = roll('bob')
    # Strong claim: with FNV-1a, 'alice' and 'bob' hashes differ enough
    # that at least one bones field differs.
    assert (
        r_alice.bones.species != r_bob.bones.species
        or r_alice.bones.eye != r_bob.bones.eye
        or r_alice.bones.rarity != r_bob.bones.rarity
    )


def test_rarity_is_valid(isolated_config: dict[str, Any]) -> None:
    """roll() always produces a rarity in RARITIES."""
    _reset_roll_cache_for_tests()
    for user in ['alice', 'bob', 'carol', 'dave', 'erin', 'frank', 'grace']:
        _reset_roll_cache_for_tests()
        r = roll(user)
        assert r.bones.rarity in RARITIES


def test_common_has_no_hat(isolated_config: dict[str, Any]) -> None:
    """TS contract (companion.ts:97): common-rarity rolls always have
    ``hat='none'``.

    Iterate a deterministic seed range; collect all common-rarity rolls
    and assert each one has hat=='none'. With common weight 60/100 and
    100 seeds, statistical probability of zero commons is ~10^-22 —
    effectively impossible but the loop still asserts ≥1 to detect a
    pathological hash-collision drift.
    """
    common_count = 0
    for i in range(100):
        r = roll_with_seed(f'seed-{i}')
        if r.bones.rarity == 'common':
            common_count += 1
            assert r.bones.hat == 'none', (
                f"seed-{i}: common rarity with non-none hat {r.bones.hat!r}"
            )
    assert common_count >= 1, (
        "expected at least one 'common' across 100 deterministic seeds — "
        "potential PRNG drift if zero"
    )


def test_get_or_create_user_id_persists_when_absent(
    isolated_config: dict[str, Any],
) -> None:
    """When `config['user_id']` is missing, generate hex and persist."""
    assert 'user_id' not in isolated_config
    uid = _get_or_create_user_id()
    assert isinstance(uid, str)
    # secrets.token_hex(32) returns 64 hex chars
    assert len(uid) == 64
    assert isolated_config['user_id'] == uid


def test_get_or_create_user_id_returns_existing(
    isolated_config: dict[str, Any],
) -> None:
    """When `config['user_id']` exists, return it without writing."""
    isolated_config['user_id'] = 'pre-existing-id'
    uid = _get_or_create_user_id()
    assert uid == 'pre-existing-id'


def test_companion_user_id_uses_config_user_id(
    isolated_config: dict[str, Any],
) -> None:
    isolated_config['user_id'] = 'fixed-id'
    assert companion_user_id() == 'fixed-id'


def test_get_companion_returns_none_when_no_stored(
    isolated_config: dict[str, Any],
) -> None:
    assert get_companion() is None


def test_get_companion_merges_soul_and_bones(
    isolated_config: dict[str, Any],
) -> None:
    """Soul fields persist; bones regenerate deterministically."""
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark',
        'personality': 'Test personality.',
        'hatched_at': 1234567890,
    }
    _reset_roll_cache_for_tests()
    c = get_companion()
    assert c is not None
    assert c.name == 'Bytespark'
    assert c.personality == 'Test personality.'
    assert c.hatched_at == 1234567890
    # Bones should match deterministic roll for fixed-id
    expected_bones = roll('fixed-id').bones
    assert c.rarity == expected_bones.rarity
    assert c.species == expected_bones.species
