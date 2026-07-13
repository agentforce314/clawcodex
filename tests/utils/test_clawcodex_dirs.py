"""Tests for the canonical session/transcript path resolvers.

These stores used to be hardcoded to ``~/.clawcodex/sessions`` and
``~/.clawcodex/transcripts`` in ~10 writers, ignoring ``$CLAWCODEX_CONFIG_DIR``
(which config/memory/skills/auth/mcp already honor). ``get_sessions_dir()`` /
``get_transcripts_dir()`` are the single source of truth that fixed that.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.utils.clawcodex_dirs import (
    get_sessions_dir,
    get_transcripts_dir,
    get_user_config_dir,
)


def test_default_is_home_clawcodex(monkeypatch):
    """Override unset → ~/.clawcodex/{sessions,transcripts} (unchanged from the
    pre-migration hardcoded location, so existing users see no move)."""
    monkeypatch.delenv("CLAWCODEX_CONFIG_DIR", raising=False)
    home_root = Path.home() / ".clawcodex"
    assert get_sessions_dir() == home_root / "sessions"
    assert get_transcripts_dir() == home_root / "transcripts"


def test_honors_config_dir_override(tmp_path, monkeypatch):
    """Override set → stores relocate under it, consistent with config/memory."""
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    assert get_sessions_dir() == tmp_path / "sessions"
    assert get_transcripts_dir() == tmp_path / "transcripts"


def test_anchored_on_the_same_resolver_as_config(tmp_path, monkeypatch):
    """Both live directly under the user config root — so the # Environment
    prompt line (which names get_user_config_dir()) is always accurate."""
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    root = get_user_config_dir()
    assert get_sessions_dir().parent == root
    assert get_transcripts_dir().parent == root


def test_expands_user_in_override(monkeypatch):
    """A ``~``-relative override is expanduser'd (mirrors get_user_config_dir)."""
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", "~/some-cc-root")
    expected = Path(os.path.expanduser("~/some-cc-root"))
    assert get_sessions_dir() == expected / "sessions"
    assert get_transcripts_dir() == expected / "transcripts"
