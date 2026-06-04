"""Tests for ``src.bridge.trusted_device`` (Phase 10 stub)."""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch

from src.bridge.trusted_device import (
    clear_trusted_device_token,
    clear_trusted_device_token_cache,
    enroll_trusted_device,
    get_trusted_device_token,
)


def test_get_token_returns_none_when_env_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_TRUSTED_DEVICE_TOKEN'}
    with patch.dict(os.environ, env, clear=True):
        assert get_trusted_device_token() is None


def test_get_token_returns_env_var_when_set() -> None:
    env = {'CLAUDE_TRUSTED_DEVICE_TOKEN': 'tdt-abc'}
    with patch.dict(os.environ, env, clear=True):
        assert get_trusted_device_token() == 'tdt-abc'


def test_get_token_treats_empty_string_as_unset() -> None:
    env = {'CLAUDE_TRUSTED_DEVICE_TOKEN': ''}
    with patch.dict(os.environ, env, clear=True):
        assert get_trusted_device_token() is None


def test_clear_token_cache_is_noop() -> None:
    """Cache clear is a no-op in env-var-only build."""
    clear_trusted_device_token_cache()


def test_clear_token_is_noop() -> None:
    """Token clear is a no-op until Phase 10 keychain integration."""
    clear_trusted_device_token()


@pytest.mark.asyncio
async def test_enroll_is_noop_stub(caplog: pytest.LogCaptureFixture) -> None:
    """Enrollment emits a debug log + returns; does not raise."""
    with caplog.at_level('DEBUG', logger='src.bridge.trusted_device'):
        await enroll_trusted_device()
    assert any('no-op stub' in r.message for r in caplog.records)
