"""Shared fixtures for ``tests/buddy/``.

Most tests need an in-memory config (mocking ``load_config`` and
``_get_default_manager``) to avoid touching the real
``~/.clawcodex/config.json``. The ``isolated_config`` fixture below
gives every test a fresh empty config; tests can pre-populate fields
on the returned dict before the system-under-test reads it.

Also resets the buddy roll cache between tests — module-level cache
state would otherwise leak across tests that mock distinct user_ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeConfigManager:
    """In-memory ``ConfigManager`` substitute.

    Mirrors the small subset of ``src.config.ConfigManager`` that the
    buddy code reads: ``get_merged()`` returns the stored dict;
    ``set_global(key, value)`` updates it and clears no cache (there
    isn't one).
    """
    _data: dict[str, Any] = field(default_factory=dict)

    def get_merged(self) -> dict[str, Any]:
        return dict(self._data)

    def load_global(self) -> dict[str, Any]:
        return dict(self._data)

    def save_global(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def set_global(self, key: str, value: Any) -> None:
        self._data[key] = value

    def invalidate(self) -> None:
        return


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Return a mutable in-memory config visible to all `src.config`
    consumers in the buddy subsystem.

    Yields the underlying dict so tests can pre-populate fields:

        def test_X(isolated_config):
            isolated_config['companion'] = {...}
            ...
    """
    fake = FakeConfigManager()

    def _load_config() -> dict[str, Any]:
        return fake.get_merged()

    def _get_default_manager() -> FakeConfigManager:
        return fake

    monkeypatch.setattr('src.config.load_config', _load_config)
    monkeypatch.setattr('src.config._get_default_manager', _get_default_manager)
    # Many buddy modules `from src.config import load_config` — re-bind
    # at each module that did a `from src.config import load_config` so
    # they pick up the fake. Modules that do a late
    # `from src.config import _get_default_manager` are covered by the
    # `src.config._get_default_manager` patch above.
    monkeypatch.setattr('src.buddy.companion.load_config', _load_config)
    monkeypatch.setattr('src.buddy.prompt.load_config', _load_config)
    monkeypatch.setattr('src.buddy.observer.load_config', _load_config)
    monkeypatch.setattr(
        'src.command_system.buddy_command.load_config', _load_config,
    )
    monkeypatch.setattr(
        'src.command_system.buddy_command._get_default_manager',
        _get_default_manager,
    )
    return fake._data


@pytest.fixture(autouse=True)
def _reset_buddy_roll_cache() -> None:
    """Reset the module-level ``_roll_cache`` between every test."""
    from src.buddy.companion import _reset_roll_cache_for_tests
    _reset_roll_cache_for_tests()
    yield
    _reset_roll_cache_for_tests()
