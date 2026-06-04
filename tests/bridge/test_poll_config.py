"""Tests for ``src.bridge.poll_config``.

Field-level validation, cross-field validators, fall-back-to-defaults
behavior, and the at-capacity liveness invariant from TS Zod schema
``pollConfig.ts:74-91``.
"""

from __future__ import annotations

from src.bridge.poll_config import (
    get_poll_interval_config,
    validate_poll_interval_config_raw,
)
from src.bridge.poll_config_defaults import DEFAULT_POLL_CONFIG


def _good_raw() -> dict[str, int]:
    """Minimum valid raw payload (matches defaults)."""
    return {
        'poll_interval_ms_not_at_capacity': 2000,
        'poll_interval_ms_at_capacity': 600_000,
        'non_exclusive_heartbeat_interval_ms': 0,
        'multisession_poll_interval_ms_not_at_capacity': 2000,
        'multisession_poll_interval_ms_partial_capacity': 2000,
        'multisession_poll_interval_ms_at_capacity': 600_000,
        'reclaim_older_than_ms': 5000,
        'session_keepalive_interval_v2_ms': 120_000,
    }


def test_get_poll_interval_config_returns_defaults() -> None:
    assert get_poll_interval_config() == DEFAULT_POLL_CONFIG


def test_validate_raw_well_formed_returns_validated_dataclass() -> None:
    out = validate_poll_interval_config_raw(_good_raw())
    assert out == DEFAULT_POLL_CONFIG


def test_validate_raw_rejects_below_floor_seek_interval() -> None:
    """``poll_interval_ms_not_at_capacity`` floor 100ms."""
    raw = _good_raw()
    raw['poll_interval_ms_not_at_capacity'] = 50
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_validate_raw_accepts_zero_at_capacity_with_heartbeat() -> None:
    """At-cap = 0 (disabled) requires heartbeat > 0 for liveness."""
    raw = _good_raw()
    raw['poll_interval_ms_at_capacity'] = 0
    raw['multisession_poll_interval_ms_at_capacity'] = 0
    raw['non_exclusive_heartbeat_interval_ms'] = 60_000
    out = validate_poll_interval_config_raw(raw)
    assert out.poll_interval_ms_at_capacity == 0
    assert out.non_exclusive_heartbeat_interval_ms == 60_000


def test_validate_raw_rejects_unit_confusion() -> None:
    """``poll_interval_ms_at_capacity`` must be 0 OR >= 100; 50 → reject."""
    raw = _good_raw()
    raw['poll_interval_ms_at_capacity'] = 50
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_validate_raw_rejects_all_zero_liveness_single_session() -> None:
    """Cross-field invariant: heartbeat=0 AND at-cap=0 is rejected."""
    raw = _good_raw()
    raw['non_exclusive_heartbeat_interval_ms'] = 0
    raw['poll_interval_ms_at_capacity'] = 0
    # Keep multisession heartbeat OR multisession_at_capacity satisfied so
    # the single-session refine is the first to trigger.
    raw['multisession_poll_interval_ms_at_capacity'] = 600_000
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_validate_raw_rejects_all_zero_liveness_multisession() -> None:
    """Same invariant for multisession path."""
    raw = _good_raw()
    raw['non_exclusive_heartbeat_interval_ms'] = 0
    raw['multisession_poll_interval_ms_at_capacity'] = 0
    # Keep single-session liveness so multisession refine is the first to trigger.
    raw['poll_interval_ms_at_capacity'] = 600_000
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_validate_raw_non_dict_returns_defaults() -> None:
    assert validate_poll_interval_config_raw(None) == DEFAULT_POLL_CONFIG
    assert validate_poll_interval_config_raw('garbage') == DEFAULT_POLL_CONFIG
    assert validate_poll_interval_config_raw([1, 2]) == DEFAULT_POLL_CONFIG


def test_validate_raw_partial_input_uses_field_defaults() -> None:
    """A partial payload merges with field defaults (no fall-back)."""
    raw = {'non_exclusive_heartbeat_interval_ms': 30_000}
    out = validate_poll_interval_config_raw(raw)
    # Custom field honored.
    assert out.non_exclusive_heartbeat_interval_ms == 30_000
    # Field-level defaults filled in.
    assert out.poll_interval_ms_at_capacity == 600_000
    assert out.reclaim_older_than_ms == 5000


def test_validate_raw_rejects_reclaim_below_one() -> None:
    """``reclaim_older_than_ms`` floor 1ms (server's ge=1 constraint)."""
    raw = _good_raw()
    raw['reclaim_older_than_ms'] = 0
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_validate_raw_accepts_session_keepalive_zero() -> None:
    """``session_keepalive_interval_v2_ms`` 0 = disabled — allowed."""
    raw = _good_raw()
    raw['session_keepalive_interval_v2_ms'] = 0
    out = validate_poll_interval_config_raw(raw)
    assert out.session_keepalive_interval_v2_ms == 0


def test_validate_raw_rejects_unit_confusion_multisession() -> None:
    """Multisession at-cap also enforces zero-or-≥100 rule."""
    raw = _good_raw()
    raw['multisession_poll_interval_ms_at_capacity'] = 50
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_strict_mode_rejects_string_for_int_field() -> None:
    """``strict=True`` mirrors Zod ``z.number()`` — no string coercion."""
    raw = _good_raw()
    raw['poll_interval_ms_not_at_capacity'] = '2000'  # type: ignore[assignment]
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_strict_mode_rejects_float_for_int_field() -> None:
    """``strict=True`` rejects floats for int fields."""
    raw = _good_raw()
    raw['poll_interval_ms_at_capacity'] = 600_000.5  # type: ignore[assignment]
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG


def test_combined_unit_confusion_and_liveness_fail_falls_back() -> None:
    """Any combination of validation failures falls back to defaults.

    Per CRITIC feedback: the specific ordering of which validator fires
    first doesn't matter; what matters is that *any* failure yields
    defaults. Pins the contract.
    """
    raw = _good_raw()
    raw['non_exclusive_heartbeat_interval_ms'] = 0
    raw['multisession_poll_interval_ms_at_capacity'] = 50  # unit-confusion
    raw['poll_interval_ms_at_capacity'] = 0  # liveness fails
    assert validate_poll_interval_config_raw(raw) == DEFAULT_POLL_CONFIG
