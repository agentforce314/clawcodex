"""Tests for ``src.bridge.session_id_compat`` and the extended exceptions."""

from __future__ import annotations

import pytest

from src.bridge.exceptions import BridgeFatalError
from src.bridge.session_id_compat import (
    _reset_shim_gate_for_testing,
    set_cse_shim_gate,
    to_compat_session_id,
    to_infra_session_id,
)


@pytest.fixture(autouse=True)
def reset_shim() -> None:
    """Each test starts with the gate unset (default-active shim)."""
    _reset_shim_gate_for_testing()


# ---------------------------------------------------------------------------
# session_id_compat
# ---------------------------------------------------------------------------


def test_to_compat_session_id_translates_cse_to_session() -> None:
    assert to_compat_session_id('cse_abc123') == 'session_abc123'


def test_to_compat_session_id_idempotent_on_session_id() -> None:
    """Already-session_ IDs pass through unchanged."""
    assert to_compat_session_id('session_xyz') == 'session_xyz'


def test_to_compat_session_id_idempotent_on_unprefixed() -> None:
    """Bare UUIDs (no prefix) pass through unchanged."""
    assert to_compat_session_id('abc123') == 'abc123'


def test_to_compat_session_id_gate_disabled_returns_input() -> None:
    """When the shim gate returns False, cse_* IDs pass through unchanged."""
    set_cse_shim_gate(lambda: False)
    assert to_compat_session_id('cse_abc') == 'cse_abc'


def test_to_compat_session_id_gate_enabled_translates() -> None:
    """When the shim gate returns True (explicit), translation still happens."""
    set_cse_shim_gate(lambda: True)
    assert to_compat_session_id('cse_abc') == 'session_abc'


def test_to_infra_session_id_translates_session_to_cse() -> None:
    assert to_infra_session_id('session_abc') == 'cse_abc'


def test_to_infra_session_id_not_gated_by_shim() -> None:
    """Even when shim is disabled, session_ → cse_ translation still happens.

    Mirrors TS comment at ``sessionIdCompat.ts:54`` — the infra direction is
    NOT gated because infrastructure-layer calls always need the cse_* tag.
    """
    set_cse_shim_gate(lambda: False)
    assert to_infra_session_id('session_abc') == 'cse_abc'


def test_to_infra_session_id_idempotent_on_cse() -> None:
    assert to_infra_session_id('cse_abc') == 'cse_abc'


def test_to_infra_session_id_idempotent_on_unprefixed() -> None:
    assert to_infra_session_id('xyz123') == 'xyz123'


def test_round_trip_cse_to_session_and_back() -> None:
    """compat(infra(session_X)) round-trip returns the original."""
    original = 'session_uuid-123'
    infra = to_infra_session_id(original)
    assert infra == 'cse_uuid-123'
    back = to_compat_session_id(infra)
    assert back == original


# ---------------------------------------------------------------------------
# Extended BridgeFatalError
# ---------------------------------------------------------------------------


def test_bridge_fatal_error_carries_status_and_type() -> None:
    err = BridgeFatalError('Poll 401', status=401, error_type='unauthorized')
    assert err.status == 401
    assert err.error_type == 'unauthorized'
    assert str(err) == 'Poll 401'


def test_bridge_fatal_error_optional_error_type() -> None:
    """``error_type`` defaults to None when server returns no error body."""
    err = BridgeFatalError('Poll 503', status=503)
    assert err.status == 503
    assert err.error_type is None


def test_bridge_fatal_error_is_exception() -> None:
    """Must be raisable and catchable like any Python exception."""
    with pytest.raises(BridgeFatalError) as exc_info:
        raise BridgeFatalError('test', status=410, error_type='environment_expired')
    assert exc_info.value.status == 410
    assert exc_info.value.error_type == 'environment_expired'


def test_bridge_fatal_error_repr_includes_fields() -> None:
    err = BridgeFatalError('Poll 401', status=401, error_type='lifetime')
    r = repr(err)
    assert 'status=401' in r
    assert 'lifetime' in r
