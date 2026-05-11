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
    cache_break: bool = False
    reason: str | None = None


def system_prompt_section(
    name: str,
    *,
    content: str,
    cache_scope: CacheScope = CacheScope.SESSION,
    order: int = 0,
) -> SystemPromptSection:
    """Build a cacheable prompt section.

    Mirrors TS ``systemPromptSection`` from
    ``constants/systemPromptSections.ts``. Sections built through this
    factory have ``cache_break = False``; their content is memoized until
    ``clear_system_prompt_sections()`` is called (on /clear or /compact).
    """
    return SystemPromptSection(
        id=name,
        content=content,
        cache_scope=cache_scope,
        order=order,
        cache_break=False,
    )


def DANGEROUS_uncachedSystemPromptSection(
    name: str,
    *,
    content: str,
    reason: str,
    cache_scope: CacheScope = CacheScope.REQUEST,
    order: int = 0,
) -> SystemPromptSection:
    """Build a cache-breaking prompt section that recomputes every turn.

    Mirrors TS ``DANGEROUS_uncachedSystemPromptSection`` from
    ``constants/systemPromptSections.ts``. The loud name is deliberate (per
    chapter §"The DANGEROUS Naming Convention"): cache-breaking sections
    must surface in code review because each one risks invalidating ~50-70K
    tokens of cached prefix.

    ``reason`` is required — a blank reason raises ``ValueError``. The
    parameter is unused at runtime, but enforcing non-empty content turns
    code review from "remember to ask why" into a constructor-time invariant.
    """
    if not reason or not reason.strip():
        raise ValueError(
            "DANGEROUS_uncachedSystemPromptSection requires a non-empty "
            "reason — explain why this section cannot be cached so future "
            "readers can evaluate whether the trade-off is still warranted."
        )
    return SystemPromptSection(
        id=name,
        content=content,
        cache_scope=cache_scope,
        order=order,
        cache_break=True,
        reason=reason.strip(),
    )


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


def clear_system_prompt_sections() -> None:
    """Clear the section cache and reset sticky beta-header latches.

    Mirrors TS ``clearSystemPromptSections()`` (constants/systemPromptSections.ts).
    Called on ``/clear`` and ``/compact`` so a fresh conversation gets fresh
    evaluation of AFK / fast-mode / cache-editing / thinking-clear / 1h-cache
    eligibility *and* fresh content for memoized sections.

    Importing ``cache_state`` lazily avoids a circular dependency: cache_state
    does not import from this module, but a future change might, and the lazy
    import keeps that risk contained.
    """
    # Lazy import to keep this file free of state dependencies at import time.
    from src.state.cache_state import clear_beta_header_latches

    # Clear the module-level prompt cache used by prompt_assembly.py.
    # The cache lives on a singleton created in prompt_assembly; import
    # lazily to avoid the prompt_assembly → system_prompt_cache circular
    # dependency that the file's "from .system_prompt_cache import ..."
    # block already comments on.
    try:
        from src.context_system.prompt_assembly import get_system_prompt_cache
        get_system_prompt_cache().invalidate_all()
    except ImportError:
        # prompt_assembly isn't always importable in minimal test contexts;
        # latch clearing still proceeds.
        pass

    clear_beta_header_latches()
