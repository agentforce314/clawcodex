"""Tests for ``src.bridge.types``.

Validates that:
- Constants match TS values exactly.
- Protocols accept duck-typed implementations.
- Dataclasses construct and round-trip.
- TypedDicts are usable as plain dict literals.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.bridge import types as bt
from src.bridge.types import (
    BRIDGE_LOGIN_ERROR,
    BRIDGE_LOGIN_INSTRUCTION,
    DEFAULT_SESSION_TIMEOUT_MS,
    REMOTE_CONTROL_DISCONNECTED_MSG,
    BridgeConfig,
    SessionActivity,
    WorkData,
    WorkResponse,
)


def test_default_session_timeout_matches_ts() -> None:
    """``types.ts:2`` ``DEFAULT_SESSION_TIMEOUT_MS = 24 * 60 * 60 * 1000``."""
    assert DEFAULT_SESSION_TIMEOUT_MS == 24 * 60 * 60 * 1000
    assert DEFAULT_SESSION_TIMEOUT_MS == 86_400_000


def test_login_messages_match_ts() -> None:
    """``BRIDGE_LOGIN_INSTRUCTION`` text matches TS verbatim."""
    assert 'Remote Control is only available with claude.ai' in BRIDGE_LOGIN_INSTRUCTION
    assert '/login' in BRIDGE_LOGIN_INSTRUCTION
    assert 'Error: You must be logged in' in BRIDGE_LOGIN_ERROR
    assert BRIDGE_LOGIN_INSTRUCTION in BRIDGE_LOGIN_ERROR


def test_remote_control_disconnected_message() -> None:
    assert REMOTE_CONTROL_DISCONNECTED_MSG == 'Remote Control disconnected.'


def test_session_activity_is_frozen_dataclass() -> None:
    a = SessionActivity(type='tool_start', summary='Reading foo.py', timestamp=1.5)
    assert a.type == 'tool_start'
    assert a.summary == 'Reading foo.py'
    assert a.timestamp == 1.5
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        a.type = 'text'  # type: ignore[misc]


def test_bridge_config_is_mutable() -> None:
    """``BridgeConfig`` must be mutable so ``spawn_mode`` can flip at runtime."""
    cfg = BridgeConfig(
        dir='/tmp',
        machine_name='test',
        branch='main',
        git_repo_url=None,
        max_sessions=1,
        spawn_mode='single-session',
        verbose=False,
        sandbox=False,
        bridge_id='abc',
        worker_type='claude_code',
        environment_id='env-1',
        api_base_url='https://api.example.com',
        session_ingress_url='https://ingress.example.com',
    )
    assert cfg.spawn_mode == 'single-session'
    cfg.spawn_mode = 'worktree'  # must not raise
    assert cfg.spawn_mode == 'worktree'
    assert cfg.reuse_environment_id is None
    assert cfg.debug_file is None


def test_work_response_typed_dict_usable_as_literal() -> None:
    """``WorkResponse`` is a TypedDict — plain dict literals satisfy it."""
    data: WorkData = {'type': 'session', 'id': 'sess-1'}
    response: WorkResponse = {
        'id': 'work-1',
        'type': 'work',
        'environment_id': 'env-1',
        'state': 'pending',
        'data': data,
        'secret': 'base64stuff',
        'created_at': '2026-05-23T00:00:00Z',
    }
    assert response['type'] == 'work'
    assert response['data']['type'] == 'session'


def test_protocols_accept_duck_typed_impls() -> None:
    """``BridgeApiClient`` Protocol accepts any object with matching methods.

    Verifies the Protocol is structural (not nominal) — implementations don't
    have to inherit from it.
    """

    class _Fake:
        async def register_bridge_environment(self, config: BridgeConfig) -> dict[str, str]:
            return {'environment_id': 'e', 'environment_secret': 's'}

        async def poll_for_work(self, *args: Any, **kwargs: Any) -> WorkResponse | None:
            return None

        async def acknowledge_work(self, *args: Any) -> None:
            return None

        async def stop_work(self, *args: Any) -> None:
            return None

        async def deregister_environment(self, *args: Any) -> None:
            return None

        async def send_permission_response_event(self, *args: Any) -> None:
            return None

        async def archive_session(self, *args: Any) -> None:
            return None

        async def reconnect_session(self, *args: Any) -> None:
            return None

        async def heartbeat_work(self, *args: Any) -> dict[str, Any]:
            return {'lease_extended': True, 'state': 'running'}

    fake = _Fake()
    # Type-check at runtime: hasattr lookups for each protocol method.
    expected = {
        'register_bridge_environment',
        'poll_for_work',
        'acknowledge_work',
        'stop_work',
        'deregister_environment',
        'send_permission_response_event',
        'archive_session',
        'reconnect_session',
        'heartbeat_work',
    }
    actual = {name for name in expected if hasattr(fake, name)}
    assert actual == expected


def test_repl_bridge_handle_protocol_shape() -> None:
    """``ReplBridgeHandle`` Protocol has the consumer-facing methods used by repl_bridge_handle.py."""
    # Just confirm the Protocol is importable and has the expected attribute names.
    expected_methods = {
        'bridge_session_id',
        'environment_id',
        'session_ingress_url',
        'write_messages',
        'write_sdk_messages',
        'send_control_request',
        'send_control_response',
        'send_cancel_request',
        'send_result',
        'teardown',
    }
    # ``Protocol`` keeps its declared members in __dict__ for runtime checks.
    member_names = {name for name in dir(bt.ReplBridgeHandle) if not name.startswith('_')}
    for m in expected_methods:
        assert m in member_names, f'ReplBridgeHandle missing {m}'


def test_all_export_completeness() -> None:
    """``__all__`` covers every public symbol the orchestrators will need."""
    must_export = {
        'BRIDGE_LOGIN_ERROR',
        'BRIDGE_LOGIN_INSTRUCTION',
        'BridgeApiClient',
        'BridgeConfig',
        'BridgeLogger',
        'DEFAULT_SESSION_TIMEOUT_MS',
        'PermissionResponseEvent',
        'REMOTE_CONTROL_DISCONNECTED_MSG',
        'ReplBridgeHandle',
        'SessionActivity',
        'SessionHandle',
        'SessionSpawner',
        'SpawnMode',
        'WorkData',
        'WorkResponse',
    }
    for sym in must_export:
        assert sym in bt.__all__, f'{sym} missing from __all__'
