"""System prompt section-based caching matching TypeScript utils/queryContext.ts.

Provides per-section caching with TTL and scope (global vs per-request).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CacheScope(Enum):
    """Cache scope for prompt sections."""
    GLOBAL = "global"       # Shared across sessions (e.g. identity)
    SESSION = "session"     # Per-session (e.g. git context)
    REQUEST = "request"     # Per-request (e.g. tools may change)


@dataclass
class CachedSection:
    """A cached prompt section."""
    content: str
    scope: CacheScope
    cached_at: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0  # 5 minutes default

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds <= 0:
            return False  # Never expires
        return (time.time() - self.cached_at) > self.ttl_seconds


@dataclass
class SystemPromptSection:
    """A system prompt section with metadata."""
    id: str
    content: str
    cache_scope: CacheScope = CacheScope.SESSION
    order: int = 0  # Lower = earlier in prompt


class SystemPromptCache:
    """Cache for system prompt sections with scope-aware invalidation."""

    def __init__(self, default_ttl: float = 300.0) -> None:
        self._cache: dict[str, CachedSection] = {}
        self._default_ttl = default_ttl
        self._debug_break: bool = False

    def get(self, section_id: str) -> str | None:
        """Get a cached section, or None if expired/missing."""
        if self._debug_break:
            return None
        entry = self._cache.get(section_id)
        if entry is None or entry.is_expired:
            return None
        return entry.content

    def set(
        self,
        section_id: str,
        content: str,
        *,
        scope: CacheScope = CacheScope.SESSION,
        ttl_seconds: float | None = None,
    ) -> None:
        """Cache a section."""
        self._cache[section_id] = CachedSection(
            content=content,
            scope=scope,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self._default_ttl,
        )

    def invalidate(self, section_id: str) -> None:
        """Remove a specific section from cache."""
        self._cache.pop(section_id, None)

    def invalidate_scope(self, scope: CacheScope) -> None:
        """Remove all sections with a given scope."""
        to_remove = [k for k, v in self._cache.items() if v.scope == scope]
        for key in to_remove:
            del self._cache[key]

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    def set_debug_break(self, enabled: bool = True) -> None:
        """Enable/disable debug break mode (forces cache miss)."""
        self._debug_break = enabled

    @property
    def size(self) -> int:
        return len(self._cache)

    def get_cached_section_ids(self) -> list[str]:
        """Get list of cached (non-expired) section IDs."""
        return [
            k for k, v in self._cache.items()
            if not v.is_expired
        ]
