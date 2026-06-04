"""Tests for ``src.utils.teleport.api``."""

from __future__ import annotations

from src.utils.teleport.api import ANTHROPIC_VERSION, get_oauth_headers


def test_anthropic_version_matches_ts() -> None:
    assert ANTHROPIC_VERSION == '2023-06-01'


def test_get_oauth_headers_shape() -> None:
    h = get_oauth_headers('tok-xyz')
    assert h == {
        'Authorization': 'Bearer tok-xyz',
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01',
    }


def test_get_oauth_headers_returns_fresh_dict_each_call() -> None:
    """Callers ``.update()`` the result; must not share state."""
    a = get_oauth_headers('tok')
    b = get_oauth_headers('tok')
    a['x-test'] = 'mutated'
    assert 'x-test' not in b


def test_get_oauth_headers_includes_token_in_authorization() -> None:
    h = get_oauth_headers('xyzABC123')
    assert h['Authorization'] == 'Bearer xyzABC123'
