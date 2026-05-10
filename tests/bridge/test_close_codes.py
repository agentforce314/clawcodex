"""Tests for ``src.bridge.close_codes`` constants."""

from __future__ import annotations

from src.bridge import close_codes


def test_constants_match_typescript_source() -> None:
    """These literal values are wire-protocol surface and must not drift."""
    assert close_codes.WS_CLOSE_EPOCH_MISMATCH == 4090
    assert close_codes.WS_CLOSE_INIT_FAILURE == 4091
    assert close_codes.WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED == 4092
    assert close_codes.WS_CLOSE_PERMANENT_UNAUTHORIZED == 4003
    assert close_codes.WS_CLOSE_SESSION_NOT_FOUND == 4001


def test_codes_are_distinct() -> None:
    """Each close-code has a distinct meaning; collision would be a bug."""
    codes = {
        close_codes.WS_CLOSE_EPOCH_MISMATCH,
        close_codes.WS_CLOSE_INIT_FAILURE,
        close_codes.WS_CLOSE_RECONNECT_BUDGET_EXHAUSTED,
        close_codes.WS_CLOSE_PERMANENT_UNAUTHORIZED,
        close_codes.WS_CLOSE_SESSION_NOT_FOUND,
    }
    assert len(codes) == 5
