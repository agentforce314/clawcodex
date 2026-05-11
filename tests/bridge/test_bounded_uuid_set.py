"""Tests for ``src.bridge.bounded_uuid_set.BoundedUUIDSet``."""

from __future__ import annotations

import pytest

from src.bridge.bounded_uuid_set import DEFAULT_CAPACITY, BoundedUUIDSet


def test_default_capacity_is_2000() -> None:
    s = BoundedUUIDSet()
    assert s.capacity == 2000
    assert s.capacity == DEFAULT_CAPACITY


def test_zero_or_negative_capacity_rejected() -> None:
    with pytest.raises(ValueError):
        BoundedUUIDSet(0)
    with pytest.raises(ValueError):
        BoundedUUIDSet(-1)


def test_add_then_has_returns_true() -> None:
    s = BoundedUUIDSet(10)
    s.add('abc')
    assert s.has('abc')
    assert 'abc' in s


def test_has_returns_false_for_unknown() -> None:
    s = BoundedUUIDSet(10)
    assert not s.has('xyz')


def test_capacity_one_evicts_immediately() -> None:
    s = BoundedUUIDSet(1)
    s.add('a')
    assert s.has('a')
    s.add('b')
    assert s.has('b')
    assert not s.has('a'), 'capacity-1 set should evict the only old entry'
    assert len(s) == 1


def test_fifo_eviction_order() -> None:
    s = BoundedUUIDSet(3)
    s.add('a')
    s.add('b')
    s.add('c')
    assert len(s) == 3
    s.add('d')  # evicts 'a'
    assert not s.has('a')
    for u in ('b', 'c', 'd'):
        assert s.has(u)
    s.add('e')  # evicts 'b'
    assert not s.has('b')
    assert s.has('c')
    assert s.has('d')
    assert s.has('e')


def test_add_idempotent_no_lru_bump() -> None:
    """Re-adding an existing UUID must NOT bump its LRU position.

    Mirrors TS ``:441`` early-return-when-already-present.
    """
    s = BoundedUUIDSet(3)
    s.add('a')
    s.add('b')
    s.add('c')
    s.add('a')  # no-op — does NOT make 'a' the youngest entry
    s.add('d')  # evicts 'a' (the original oldest)
    assert not s.has('a')
    assert s.has('b')
    assert s.has('c')
    assert s.has('d')


def test_clear_resets_to_empty() -> None:
    s = BoundedUUIDSet(3)
    s.add('a')
    s.add('b')
    assert len(s) == 2
    s.clear()
    assert len(s) == 0
    assert not s.has('a')
    s.add('c')
    assert s.has('c')


def test_large_capacity_stress() -> None:
    """Add 5*N items into a capacity-N set; only the last N are present."""
    n = 100
    s = BoundedUUIDSet(n)
    for i in range(5 * n):
        s.add(f'uuid-{i}')
    assert len(s) == n
    # The last N (i = 4n .. 5n-1) should remain.
    for i in range(4 * n, 5 * n):
        assert s.has(f'uuid-{i}')
    # The first 4*N should be evicted.
    for i in range(4 * n):
        assert not s.has(f'uuid-{i}')


def test_contains_rejects_non_string() -> None:
    s = BoundedUUIDSet(3)
    s.add('a')
    assert 'a' in s
    assert 123 not in s  # non-string should not raise
    assert None not in s
