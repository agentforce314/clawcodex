"""Tests for ``src.bridge.repl_bridge_handle``."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.bridge.repl_bridge_handle import (
    _reset_for_testing,
    get_repl_bridge_handle,
    get_self_bridge_compat_id,
    set_repl_bridge_handle,
)


class _FakeHandle:
    """Minimal duck-typed ReplBridgeHandle for tests."""

    def __init__(
        self,
        bridge_session_id: str,
        environment_id: str = 'env-1',
        session_ingress_url: str = 'https://api.example.com',
    ) -> None:
        self.bridge_session_id = bridge_session_id
        self.environment_id = environment_id
        self.session_ingress_url = session_ingress_url

    write_messages = AsyncMock()
    write_sdk_messages = AsyncMock()
    send_control_request = AsyncMock()
    send_control_response = AsyncMock()
    send_cancel_request = AsyncMock()
    send_result = AsyncMock()
    teardown = AsyncMock()


@pytest.fixture(autouse=True)
def _reset_handle() -> None:
    _reset_for_testing()


def test_get_handle_default_none() -> None:
    assert get_repl_bridge_handle() is None


def test_set_and_get_handle_round_trip() -> None:
    h = _FakeHandle(bridge_session_id='cse_abc')
    set_repl_bridge_handle(h)  # type: ignore[arg-type]
    assert get_repl_bridge_handle() is h


def test_set_to_none_clears_handle() -> None:
    h = _FakeHandle(bridge_session_id='cse_abc')
    set_repl_bridge_handle(h)  # type: ignore[arg-type]
    set_repl_bridge_handle(None)
    assert get_repl_bridge_handle() is None


def test_get_self_bridge_compat_id_returns_none_when_no_handle() -> None:
    assert get_self_bridge_compat_id() is None


def test_get_self_bridge_compat_id_retags_cse_to_session() -> None:
    """``cse_uuid`` is rewritten to ``session_uuid`` for peer dedup."""
    h = _FakeHandle(bridge_session_id='cse_abc123')
    set_repl_bridge_handle(h)  # type: ignore[arg-type]
    assert get_self_bridge_compat_id() == 'session_abc123'


def test_get_self_bridge_compat_id_idempotent_on_session_prefix() -> None:
    h = _FakeHandle(bridge_session_id='session_xyz')
    set_repl_bridge_handle(h)  # type: ignore[arg-type]
    assert get_self_bridge_compat_id() == 'session_xyz'


def test_get_self_bridge_compat_id_returns_input_for_unprefixed() -> None:
    h = _FakeHandle(bridge_session_id='bare-uuid')
    set_repl_bridge_handle(h)  # type: ignore[arg-type]
    assert get_self_bridge_compat_id() == 'bare-uuid'


def test_replacement_handle_overrides_previous() -> None:
    """Calling set with a new handle replaces the old one wholesale."""
    h1 = _FakeHandle(bridge_session_id='cse_first')
    h2 = _FakeHandle(bridge_session_id='cse_second')
    set_repl_bridge_handle(h1)  # type: ignore[arg-type]
    set_repl_bridge_handle(h2)  # type: ignore[arg-type]
    assert get_repl_bridge_handle() is h2
    assert get_self_bridge_compat_id() == 'session_second'
