"""Tests for ``src.bridge.env_less_bridge_config``.

Every TS default must match the Python default exactly — behavior-
preservation contract per refactoring plan §3.9.
"""

from __future__ import annotations

import pytest

from src.bridge.env_less_bridge_config import (
    DEFAULT_ENV_LESS_BRIDGE_CONFIG,
    EnvLessBridgeConfig,
    check_env_less_bridge_min_version,
    get_env_less_bridge_config,
    should_show_app_upgrade_message,
    validate_env_less_bridge_config_raw,
)


def test_defaults_match_ts() -> None:
    """Mirrors TS ``envLessBridgeConfig.ts:44-58`` field-by-field."""
    cfg = DEFAULT_ENV_LESS_BRIDGE_CONFIG
    assert cfg.init_retry_max_attempts == 3
    assert cfg.init_retry_base_delay_ms == 500
    assert cfg.init_retry_jitter_fraction == 0.25
    assert cfg.init_retry_max_delay_ms == 4000
    assert cfg.http_timeout_ms == 10_000
    assert cfg.uuid_dedup_buffer_size == 2000
    assert cfg.heartbeat_interval_ms == 20_000
    assert cfg.heartbeat_jitter_fraction == 0.1
    assert cfg.token_refresh_buffer_ms == 300_000
    assert cfg.teardown_archive_timeout_ms == 1500
    assert cfg.connect_timeout_ms == 15_000
    assert cfg.min_version == '0.0.0'
    assert cfg.should_show_app_upgrade_message is False


@pytest.mark.asyncio
async def test_get_env_less_bridge_config_returns_defaults() -> None:
    """No GrowthBook in Python build → defaults always."""
    cfg = await get_env_less_bridge_config()
    assert cfg == DEFAULT_ENV_LESS_BRIDGE_CONFIG


@pytest.mark.asyncio
async def test_check_min_version_returns_none_for_default() -> None:
    assert await check_env_less_bridge_min_version() is None


@pytest.mark.asyncio
async def test_should_show_app_upgrade_message_false_default() -> None:
    """Default has the flag off → returns False even though v2 enabled."""
    assert await should_show_app_upgrade_message() is False


def test_validate_raw_rejects_out_of_range_heartbeat() -> None:
    """``heartbeat_interval_ms`` floor 5000 — values below fall back to defaults."""
    raw = {'heartbeat_interval_ms': 1000}
    out = validate_env_less_bridge_config_raw(raw)
    assert out == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_validate_raw_rejects_out_of_range_heartbeat_high() -> None:
    """``heartbeat_interval_ms`` cap 30000 — values above fall back to defaults."""
    raw = {'heartbeat_interval_ms': 60_000}
    out = validate_env_less_bridge_config_raw(raw)
    assert out == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_validate_raw_rejects_inverted_token_refresh_buffer() -> None:
    """Cap 1_800_000 catches the buffer-vs-delay semantic inversion."""
    raw = {'token_refresh_buffer_ms': 2_000_000}
    out = validate_env_less_bridge_config_raw(raw)
    assert out == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_validate_raw_partial_input_uses_defaults_for_omitted() -> None:
    """A valid partial override merges into defaults for omitted fields."""
    raw = {'heartbeat_interval_ms': 25_000}
    out = validate_env_less_bridge_config_raw(raw)
    assert out.heartbeat_interval_ms == 25_000
    # All other fields stay at defaults.
    assert out.connect_timeout_ms == 15_000
    assert out.token_refresh_buffer_ms == 300_000


def test_validate_raw_non_dict_returns_defaults() -> None:
    assert validate_env_less_bridge_config_raw(None) == DEFAULT_ENV_LESS_BRIDGE_CONFIG
    assert validate_env_less_bridge_config_raw('garbage') == DEFAULT_ENV_LESS_BRIDGE_CONFIG
    assert validate_env_less_bridge_config_raw([1, 2, 3]) == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_validate_raw_full_override_accepted() -> None:
    """A complete, well-formed override yields a non-default config."""
    raw = {
        'init_retry_max_attempts': 5,
        'init_retry_base_delay_ms': 1000,
        'init_retry_jitter_fraction': 0.5,
        'init_retry_max_delay_ms': 8000,
        'http_timeout_ms': 20_000,
        'uuid_dedup_buffer_size': 5000,
        'heartbeat_interval_ms': 25_000,
        'heartbeat_jitter_fraction': 0.2,
        'token_refresh_buffer_ms': 600_000,
        'teardown_archive_timeout_ms': 1800,
        'connect_timeout_ms': 30_000,
        'min_version': '1.2.3',
        'should_show_app_upgrade_message': True,
    }
    out = validate_env_less_bridge_config_raw(raw)
    assert out.init_retry_max_attempts == 5
    assert out.should_show_app_upgrade_message is True
    assert out.min_version == '1.2.3'


def test_env_less_bridge_config_constructible_directly() -> None:
    """Direct construction with kwargs works (test-helper path)."""
    cfg = EnvLessBridgeConfig(heartbeat_interval_ms=15_000)
    assert cfg.heartbeat_interval_ms == 15_000
    assert cfg.connect_timeout_ms == 15_000  # default


def test_env_less_bridge_config_rejects_out_of_range_construction() -> None:
    """Direct construction enforces bounds."""
    with pytest.raises(Exception):  # pydantic ValidationError
        EnvLessBridgeConfig(heartbeat_interval_ms=1)


def test_strict_mode_rejects_string_for_int_field() -> None:
    """``strict=True`` mirrors Zod ``z.number()`` — no string coercion.

    Regression test per CRITIC feedback: without ``strict=True``, pydantic
    silently coerces ``"5"`` to ``5``, hiding upstream GrowthBook schema
    drift. TS Zod rejects this.
    """
    raw = {'init_retry_max_attempts': '5'}
    assert validate_env_less_bridge_config_raw(raw) == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_strict_mode_rejects_float_for_int_field() -> None:
    """``strict=True`` rejects floats for int fields (no truncation)."""
    raw = {'init_retry_max_attempts': 3.5}
    assert validate_env_less_bridge_config_raw(raw) == DEFAULT_ENV_LESS_BRIDGE_CONFIG


def test_strict_mode_rejects_string_for_bool_field() -> None:
    """``strict=True`` rejects ``"true"`` as a bool."""
    raw = {'should_show_app_upgrade_message': 'true'}
    assert validate_env_less_bridge_config_raw(raw) == DEFAULT_ENV_LESS_BRIDGE_CONFIG
