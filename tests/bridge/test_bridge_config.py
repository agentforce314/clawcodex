"""Tests for ``src.bridge.bridge_config``."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.bridge.bridge_config import (
    get_bridge_access_token,
    get_bridge_base_url,
    get_bridge_base_url_override,
    get_bridge_token_override,
)


def _no_bridge_env() -> dict[str, str]:
    """Env without the CLAUDE_BRIDGE_* overrides."""
    return {
        k: v for k, v in os.environ.items()
        if not k.startswith('CLAUDE_BRIDGE_')
    }


def test_token_override_set_returns_value() -> None:
    env = _no_bridge_env() | {'CLAUDE_BRIDGE_OAUTH_TOKEN': 'tok-123'}
    with patch.dict(os.environ, env, clear=True):
        assert get_bridge_token_override() == 'tok-123'


def test_token_override_unset_returns_none() -> None:
    with patch.dict(os.environ, _no_bridge_env(), clear=True):
        assert get_bridge_token_override() is None


def test_token_override_empty_string_returns_none() -> None:
    """Empty string is treated as unset (matches TS ``|| undefined``)."""
    env = _no_bridge_env() | {'CLAUDE_BRIDGE_OAUTH_TOKEN': ''}
    with patch.dict(os.environ, env, clear=True):
        assert get_bridge_token_override() is None


def test_base_url_override_set_returns_value() -> None:
    env = _no_bridge_env() | {'CLAUDE_BRIDGE_BASE_URL': 'https://staging.example.com'}
    with patch.dict(os.environ, env, clear=True):
        assert get_bridge_base_url_override() == 'https://staging.example.com'


def test_base_url_override_unset_returns_none() -> None:
    with patch.dict(os.environ, _no_bridge_env(), clear=True):
        assert get_bridge_base_url_override() is None


def test_get_bridge_access_token_returns_override() -> None:
    env = _no_bridge_env() | {'CLAUDE_BRIDGE_OAUTH_TOKEN': 'tok-xyz'}
    with patch.dict(os.environ, env, clear=True):
        assert get_bridge_access_token() == 'tok-xyz'


def test_get_bridge_access_token_falls_through_to_none() -> None:
    """Fallthrough returns None until Phase 2 lands the OAuth keychain read."""
    with patch.dict(os.environ, _no_bridge_env(), clear=True):
        assert get_bridge_access_token() is None


def test_get_bridge_base_url_returns_override() -> None:
    env = _no_bridge_env() | {'CLAUDE_BRIDGE_BASE_URL': 'https://dev.example.com'}
    with patch.dict(os.environ, env, clear=True):
        assert get_bridge_base_url() == 'https://dev.example.com'


def test_get_bridge_base_url_falls_through_to_production() -> None:
    """Fallthrough returns the inline production constant."""
    with patch.dict(os.environ, _no_bridge_env(), clear=True):
        assert get_bridge_base_url() == 'https://api.anthropic.com'


def test_get_bridge_base_url_always_returns_non_none() -> None:
    """``get_bridge_base_url()`` must never return None — TS signature
    is ``string``, not ``string | undefined``."""
    with patch.dict(os.environ, _no_bridge_env(), clear=True):
        result = get_bridge_base_url()
    assert isinstance(result, str)
    assert len(result) > 0
