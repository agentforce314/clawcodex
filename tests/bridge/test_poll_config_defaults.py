"""Tests for ``src.bridge.poll_config_defaults``.

Each TS default must match the Python default exactly — this is a
behavior-preservation contract.
"""

from __future__ import annotations

import pytest

from src.bridge.poll_config_defaults import (
    DEFAULT_POLL_CONFIG,
    MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY,
    MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY,
    MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY,
    POLL_INTERVAL_MS_AT_CAPACITY,
    POLL_INTERVAL_MS_NOT_AT_CAPACITY,
    PollIntervalConfig,
)


def test_poll_interval_ms_not_at_capacity_is_2000() -> None:
    """Mirrors TS ``pollConfigDefaults.ts:13``."""
    assert POLL_INTERVAL_MS_NOT_AT_CAPACITY == 2000


def test_poll_interval_ms_at_capacity_is_600000() -> None:
    """Mirrors TS ``pollConfigDefaults.ts:30`` — 10 minutes."""
    assert POLL_INTERVAL_MS_AT_CAPACITY == 600_000


def test_multisession_defaults_match_single_session() -> None:
    """Mirrors TS ``pollConfigDefaults.ts:39-42``."""
    assert MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY == POLL_INTERVAL_MS_NOT_AT_CAPACITY
    assert MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY == POLL_INTERVAL_MS_NOT_AT_CAPACITY
    assert MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY == POLL_INTERVAL_MS_AT_CAPACITY


def test_default_poll_config_field_values() -> None:
    """Every field of DEFAULT_POLL_CONFIG matches TS DEFAULT_POLL_CONFIG."""
    assert DEFAULT_POLL_CONFIG.poll_interval_ms_not_at_capacity == 2000
    assert DEFAULT_POLL_CONFIG.poll_interval_ms_at_capacity == 600_000
    # 0 = heartbeat disabled by default (matches TS comment lines 58-65).
    assert DEFAULT_POLL_CONFIG.non_exclusive_heartbeat_interval_ms == 0
    assert DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_not_at_capacity == 2000
    assert DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_partial_capacity == 2000
    assert DEFAULT_POLL_CONFIG.multisession_poll_interval_ms_at_capacity == 600_000
    # 5s reclaim matches server DEFAULT_RECLAIM_OLDER_THAN_MS (work_service.py:24).
    assert DEFAULT_POLL_CONFIG.reclaim_older_than_ms == 5000
    # 2min keepalive prevents upstream-proxy GC of idle remote-control sessions.
    assert DEFAULT_POLL_CONFIG.session_keepalive_interval_v2_ms == 120_000


def test_default_poll_config_is_frozen() -> None:
    """PollIntervalConfig is frozen; field assignment raises."""
    with pytest.raises(Exception):  # FrozenInstanceError
        DEFAULT_POLL_CONFIG.poll_interval_ms_at_capacity = 999  # type: ignore[misc]


def test_poll_interval_config_constructible() -> None:
    """PollIntervalConfig can be constructed with custom values."""
    cfg = PollIntervalConfig(
        poll_interval_ms_not_at_capacity=1000,
        poll_interval_ms_at_capacity=10_000,
        non_exclusive_heartbeat_interval_ms=20_000,
        multisession_poll_interval_ms_not_at_capacity=1000,
        multisession_poll_interval_ms_partial_capacity=1000,
        multisession_poll_interval_ms_at_capacity=10_000,
        reclaim_older_than_ms=5000,
        session_keepalive_interval_v2_ms=60_000,
    )
    assert cfg.poll_interval_ms_at_capacity == 10_000


def test_at_capacity_liveness_invariant_default() -> None:
    """Default config satisfies the at-capacity liveness invariant.

    Mirrors TS ``pollConfig.ts:74-91`` refine: either heartbeat > 0 OR
    poll_interval_ms_at_capacity > 0. The default has heartbeat=0 but
    poll_interval_ms_at_capacity=600000 — invariant holds via the second clause.
    """
    cfg = DEFAULT_POLL_CONFIG
    assert (
        cfg.non_exclusive_heartbeat_interval_ms > 0
        or cfg.poll_interval_ms_at_capacity > 0
    )
    assert (
        cfg.non_exclusive_heartbeat_interval_ms > 0
        or cfg.multisession_poll_interval_ms_at_capacity > 0
    )
