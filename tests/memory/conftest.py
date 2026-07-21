"""Shared isolation for the bounded-memory tests: every test gets a fresh
``$CLAWCODEX_CONFIG_DIR`` and a dropped store singleton, so nothing ever
touches the developer's real ``~/.clawcodex/memories``."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def memory_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "cfg"))
    from src.memory import reset_memory_store_cache

    reset_memory_store_cache()
    yield tmp_path / "cfg"
    reset_memory_store_cache()
