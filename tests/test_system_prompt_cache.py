"""Tests for R2-WS-5: System prompt cache."""

from __future__ import annotations

import time

import pytest

from src.context_system.system_prompt_cache import (
    CacheScope,
    CachedSection,
    SystemPromptCache,
    SystemPromptSection,
)


class TestCachedSection:
    def test_not_expired(self):
        section = CachedSection(content="test", scope=CacheScope.SESSION, ttl_seconds=300)
        assert section.is_expired is False

    def test_expired(self):
        section = CachedSection(
            content="test",
            scope=CacheScope.SESSION,
            cached_at=time.time() - 400,
            ttl_seconds=300,
        )
        assert section.is_expired is True

    def test_never_expires(self):
        section = CachedSection(
            content="test",
            scope=CacheScope.GLOBAL,
            cached_at=time.time() - 999999,
            ttl_seconds=-1,
        )
        assert section.is_expired is False


class TestSystemPromptCache:
    def test_set_and_get(self):
        cache = SystemPromptCache()
        cache.set("test", "hello", scope=CacheScope.SESSION)
        assert cache.get("test") == "hello"

    def test_get_missing(self):
        cache = SystemPromptCache()
        assert cache.get("nonexistent") is None

    def test_invalidate(self):
        cache = SystemPromptCache()
        cache.set("a", "value")
        cache.invalidate("a")
        assert cache.get("a") is None

    def test_invalidate_scope(self):
        cache = SystemPromptCache()
        cache.set("s1", "v1", scope=CacheScope.SESSION)
        cache.set("s2", "v2", scope=CacheScope.SESSION)
        cache.set("g1", "v3", scope=CacheScope.GLOBAL)
        cache.invalidate_scope(CacheScope.SESSION)
        assert cache.get("s1") is None
        assert cache.get("s2") is None
        assert cache.get("g1") == "v3"

    def test_invalidate_all(self):
        cache = SystemPromptCache()
        cache.set("a", "1")
        cache.set("b", "2")
        cache.invalidate_all()
        assert cache.size == 0

    def test_debug_break_mode(self):
        cache = SystemPromptCache()
        cache.set("test", "value")
        assert cache.get("test") == "value"

        cache.set_debug_break(True)
        assert cache.get("test") is None

        cache.set_debug_break(False)
        assert cache.get("test") == "value"

    def test_ttl_expiration(self):
        cache = SystemPromptCache(default_ttl=0.01)  # 10ms
        cache.set("fast", "value")
        assert cache.get("fast") == "value"
        time.sleep(0.02)
        assert cache.get("fast") is None

    def test_custom_ttl_per_section(self):
        cache = SystemPromptCache(default_ttl=300)
        cache.set("short", "value", ttl_seconds=0.01)
        cache.set("long", "value", ttl_seconds=300)
        time.sleep(0.02)
        assert cache.get("short") is None
        assert cache.get("long") == "value"

    def test_get_cached_section_ids(self):
        cache = SystemPromptCache()
        cache.set("a", "1")
        cache.set("b", "2")
        ids = cache.get_cached_section_ids()
        assert set(ids) == {"a", "b"}

    def test_size(self):
        cache = SystemPromptCache()
        assert cache.size == 0
        cache.set("a", "1")
        assert cache.size == 1
        cache.set("b", "2")
        assert cache.size == 2


class TestSystemPromptSection:
    def test_creation(self):
        section = SystemPromptSection(
            id="identity",
            content="You are Claude.",
            cache_scope=CacheScope.GLOBAL,
            order=0,
        )
        assert section.id == "identity"
        assert section.content == "You are Claude."
        assert section.cache_scope == CacheScope.GLOBAL
        assert section.order == 0

    def test_default_scope(self):
        section = SystemPromptSection(id="test", content="content")
        assert section.cache_scope == CacheScope.SESSION
