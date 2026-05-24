from __future__ import annotations

"""
Extension Layer Cache Management

Provides caching for skill discovery and loading to improve performance.
Mirrors patterns from tool_system_ext/cache.py.
"""

import time
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class CacheEntry(Generic[T]):
    """A single cache entry with expiration."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: T, ttl_seconds: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl_seconds

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class SkillCache:
    """
    Generic cache with TTL support for skill operations.

    Attributes:
        default_ttl: Default time-to-live in seconds (default: 300)
    """

    def __init__(self, default_ttl: float = 300.0) -> None:
        self._store: dict[str, CacheEntry[Any]] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """
        Get a value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """
        Set a value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds, uses default if None
        """
        ttl_seconds = ttl if ttl is not None else self.default_ttl
        self._store[key] = CacheEntry(value, ttl_seconds)

    def invalidate(self, key: str) -> None:
        """Remove a key from cache."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._store.clear()

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed
        """
        expired_keys = [
            k for k, v in self._store.items() if v.is_expired()
        ]
        for key in expired_keys:
            del self._store[key]
        return len(expired_keys)


# Global caches for skill operations
_skill_discovery_cache = SkillCache(default_ttl=60.0)
_skill_registry_cache = SkillCache(default_ttl=300.0)


def get_discovery_cache() -> SkillCache:
    """Get the skill discovery cache."""
    return _skill_discovery_cache


def get_registry_cache() -> SkillCache:
    """Get the skill registry cache."""
    return _skill_registry_cache


def clear_all_caches() -> None:
    """Clear all global skill caches."""
    _skill_discovery_cache.clear()
    _skill_registry_cache.clear()