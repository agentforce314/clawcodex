"""Tests for ``src.bridge.bridge_enabled`` (all stubs)."""

from __future__ import annotations

import pytest

from src.bridge import bridge_enabled as be


def test_is_bridge_enabled_true() -> None:
    assert be.is_bridge_enabled() is True


@pytest.mark.asyncio
async def test_is_bridge_enabled_blocking_true() -> None:
    assert await be.is_bridge_enabled_blocking() is True


@pytest.mark.asyncio
async def test_get_bridge_disabled_reason_none() -> None:
    assert await be.get_bridge_disabled_reason() is None


def test_is_env_less_bridge_enabled_true() -> None:
    assert be.is_env_less_bridge_enabled() is True


def test_is_cse_shim_enabled_true() -> None:
    assert be.is_cse_shim_enabled() is True


def test_check_bridge_min_version_none() -> None:
    assert be.check_bridge_min_version() is None


def test_get_ccr_auto_connect_default_false() -> None:
    assert be.get_ccr_auto_connect_default() is False


def test_is_ccr_mirror_enabled_false() -> None:
    assert be.is_ccr_mirror_enabled() is False


def test_all_exports_present() -> None:
    expected = {
        'check_bridge_min_version',
        'get_bridge_disabled_reason',
        'get_ccr_auto_connect_default',
        'is_bridge_enabled',
        'is_bridge_enabled_blocking',
        'is_ccr_mirror_enabled',
        'is_cse_shim_enabled',
        'is_env_less_bridge_enabled',
    }
    assert set(be.__all__) == expected
