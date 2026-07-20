"""The ``memory_store`` system-prompt section (prompt_assembly): guidance
always present when enabled, snapshot content included, SESSION scope +
ordering after auto-memory, settings gates, and the rebuild-refresh
semantics (frozen per build, fresh on the next build)."""

from __future__ import annotations

from unittest.mock import patch

from src.context_system.prompt_assembly import (
    _build_memory_store_section,
    build_full_system_prompt_blocks,
)
from src.context_system.system_prompt_cache import CacheScope
from src.memory import get_memory_store


class _Settings:
    memory_store_enabled = True
    user_profile_enabled = True
    memory_char_limit = 2200
    user_char_limit = 1375


class TestSection:
    def test_guidance_present_when_store_empty(self):
        s = _build_memory_store_section()
        assert s is not None
        assert "# Persistent Memory" in s.content
        assert "declarative facts" in s.content
        assert s.cache_scope is CacheScope.SESSION
        assert s.order == 26

    def test_snapshot_content_included_on_build(self):
        get_memory_store().add("memory", "Repo uses ruff")
        get_memory_store().add("user", "Name is Sam")
        s = _build_memory_store_section()
        assert "Repo uses ruff" in s.content
        assert "MEMORY (your personal notes)" in s.content
        assert "Name is Sam" in s.content
        assert "USER PROFILE (who the user is)" in s.content

    def test_each_build_captures_fresh_disk_state(self):
        # Freshness semantics: the section reloads at every BUILD (each
        # build is a cache-boundary event); within a session the built
        # prompt string is cached by the caller, giving the frozen span.
        s1 = _build_memory_store_section()
        assert "late entry" not in s1.content
        get_memory_store().add("memory", "late entry")
        s2 = _build_memory_store_section()
        assert "late entry" in s2.content

    def test_store_disabled_removes_section(self):
        class _Off(_Settings):
            memory_store_enabled = False

        with patch("src.settings.settings.get_settings", return_value=_Off()):
            assert _build_memory_store_section() is None

    def test_user_profile_disabled_drops_user_block_only(self):
        get_memory_store().add("memory", "a note")
        get_memory_store().add("user", "a profile fact")

        class _NoProfile(_Settings):
            user_profile_enabled = False

        with patch("src.settings.settings.get_settings", return_value=_NoProfile()):
            from src.memory import reset_memory_store_cache

            reset_memory_store_cache()
            s = _build_memory_store_section()
        assert "a note" in s.content
        assert "a profile fact" not in s.content

    def test_present_in_full_prompt_blocks(self):
        get_memory_store().add("memory", "block-level fact")
        blocks = build_full_system_prompt_blocks(cwd="/tmp")
        joined = "\n".join(b.get("text", "") for b in blocks)
        assert "# Persistent Memory" in joined
        assert "block-level fact" in joined
