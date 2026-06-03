"""Tests for F-12: cacheWarning capacity limit."""

from __future__ import annotations

import pytest

from src.utils.cache_warning import (
    CacheWarning,
    CacheWarningState,
    MAX_SOURCE_ENTRIES,
)


class TestCacheWarningState:
    """Tests for CacheWarningState dataclass."""

    def test_default_values(self):
        state = CacheWarningState()
        assert state.warned is False
        assert state.count == 0

    def test_custom_values(self):
        state = CacheWarningState(warned=True, count=42)
        assert state.warned is True
        assert state.count == 42


class TestCacheWarning:
    """Tests for CacheWarning class with LRU eviction."""

    def test_init_empty(self):
        cache = CacheWarning()
        assert len(cache.cache_warning_state_by_source) == 0

    def test_update_and_get(self):
        cache = CacheWarning()
        state = CacheWarningState(warned=True, count=1)
        cache.update("source_a", state)

        result = cache.get("source_a")
        assert result is not None
        assert result.warned is True
        assert result.count == 1

    def test_get_missing_returns_none(self):
        cache = CacheWarning()
        result = cache.get("nonexistent")
        assert result is None

    def test_update_overwrites_existing(self):
        cache = CacheWarning()
        cache.update("source_a", CacheWarningState(warned=False, count=1))
        cache.update("source_a", CacheWarningState(warned=True, count=2))

        result = cache.get("source_a")
        assert result is not None
        assert result.warned is True
        assert result.count == 2

    def test_eviction_at_capacity(self):
        cache = CacheWarning()

        # Fill to exactly MAX_SOURCE_ENTRIES
        for i in range(MAX_SOURCE_ENTRIES):
            cache.update(f"source_{i}", CacheWarningState(warned=False, count=i))

        assert len(cache.cache_warning_state_by_source) == MAX_SOURCE_ENTRIES
        assert cache.get("source_0") is not None  # Oldest still present

        # Adding one more should evict the oldest (source_0)
        cache.update("source_extra", CacheWarningState(warned=False, count=100))

        assert len(cache.cache_warning_state_by_source) == MAX_SOURCE_ENTRIES
        assert cache.get("source_0") is None  # Evicted
        assert cache.get("source_extra") is not None  # New entry present

    def test_boundary_exactly_fifty_no_eviction(self):
        """At exactly 50 entries, no eviction should occur."""
        cache = CacheWarning()

        for i in range(50):
            cache.update(f"source_{i}", CacheWarningState(warned=False, count=i))

        assert len(cache.cache_warning_state_by_source) == 50
        assert cache.get("source_0") is not None
        assert cache.get("source_49") is not None

    def test_boundary_fifty_first_triggers_eviction(self):
        """The 51st entry should trigger eviction of the oldest."""
        cache = CacheWarning()

        for i in range(50):
            cache.update(f"source_{i}", CacheWarningState(warned=False, count=i))

        # This should trigger eviction
        cache.update("source_50", CacheWarningState(warned=False, count=50))

        assert len(cache.cache_warning_state_by_source) == 50
        assert cache.get("source_0") is None  # First entry evicted
        assert cache.get("source_1") is not None  # Second entry still present
        assert cache.get("source_50") is not None  # New entry present

    def test_reset_for_test(self):
        cache = CacheWarning()
        cache.update("source_a", CacheWarningState(warned=True, count=1))
        cache.update("source_b", CacheWarningState(warned=True, count=2))

        assert len(cache.cache_warning_state_by_source) == 2

        cache.reset_for_test()

        assert len(cache.cache_warning_state_by_source) == 0
        assert cache.get("source_a") is None
        assert cache.get("source_b") is None

    def test_multiple_evictions(self):
        """Verify eviction happens correctly over multiple cycles."""
        cache = CacheWarning()

        # Add 100 entries, should trigger eviction twice
        for i in range(100):
            cache.update(f"source_{i}", CacheWarningState(warned=False, count=i))

        # Should have exactly MAX_SOURCE_ENTRIES entries
        assert len(cache.cache_warning_state_by_source) == MAX_SOURCE_ENTRIES

        # Entries 0-49 should be evicted (first 50 entries)
        for i in range(50):
            assert cache.get(f"source_{i}") is None

        # Entries 50-99 should still be present
        for i in range(50, 100):
            assert cache.get(f"source_{i}") is not None

    def test_fifo_order(self):
        """Verify entries are evicted in insertion order (FIFO)."""
        cache = CacheWarning()

        cache.update("first", CacheWarningState(warned=False, count=1))
        cache.update("second", CacheWarningState(warned=False, count=2))
        cache.update("third", CacheWarningState(warned=False, count=3))

        # Add entries to trigger eviction
        for i in range(50):
            cache.update(f"extra_{i}", CacheWarningState(warned=False, count=i))

        # First three entries should be evicted in order
        assert cache.get("first") is None
        assert cache.get("second") is None
        assert cache.get("third") is None

        # Most recent entries should still be present
        assert cache.get("extra_49") is not None


class TestMaxSourceEntries:
    """Tests for MAX_SOURCE_ENTRIES constant."""

    def test_max_source_entries_value(self):
        """Verify the capacity limit is 50 as specified."""
        assert MAX_SOURCE_ENTRIES == 50