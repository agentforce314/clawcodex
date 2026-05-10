"""Tests for ``src.server.types``."""

from __future__ import annotations

import pytest

from src.server.types import (
    SessionState,
    validate_connect_response,
)


class TestSessionState:
    def test_five_state_lifecycle(self) -> None:
        """Mirror TS server/types.ts:26-31."""
        assert {s.value for s in SessionState} == {
            'starting', 'running', 'detached', 'stopping', 'stopped'
        }

    def test_serializes_as_string(self) -> None:
        assert SessionState.RUNNING.value == 'running'
        assert str(SessionState.RUNNING.value) == 'running'


class TestValidateConnectResponse:
    def test_minimal_valid_payload(self) -> None:
        out = validate_connect_response({'session_id': 'cse_abc', 'ws_url': 'ws://x:1/ws/y'})
        assert out['session_id'] == 'cse_abc'
        assert out['ws_url'] == 'ws://x:1/ws/y'
        assert 'work_dir' not in out

    def test_with_optional_work_dir(self) -> None:
        out = validate_connect_response({
            'session_id': 's', 'ws_url': 'ws://x:1/ws/s', 'work_dir': '/tmp',
        })
        assert out['work_dir'] == '/tmp'

    def test_rejects_non_dict(self) -> None:
        with pytest.raises(ValueError, match='must be an object'):
            validate_connect_response([])

    def test_rejects_missing_session_id(self) -> None:
        with pytest.raises(ValueError, match='session_id'):
            validate_connect_response({'ws_url': 'ws://x'})

    def test_rejects_empty_session_id(self) -> None:
        with pytest.raises(ValueError, match='session_id'):
            validate_connect_response({'session_id': '', 'ws_url': 'ws://x'})

    def test_rejects_missing_ws_url(self) -> None:
        with pytest.raises(ValueError, match='ws_url'):
            validate_connect_response({'session_id': 's'})

    def test_rejects_non_string_work_dir(self) -> None:
        with pytest.raises(ValueError, match='work_dir'):
            validate_connect_response({
                'session_id': 's', 'ws_url': 'ws://x', 'work_dir': 42,
            })
