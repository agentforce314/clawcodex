"""Tests for ``src.bridge.bridge_permission_callbacks``."""

from __future__ import annotations

from src.bridge.bridge_permission_callbacks import (
    BridgePermissionCallbacks,
    BridgePermissionResponse,
    is_bridge_permission_response,
)


def test_is_bridge_permission_response_accepts_allow() -> None:
    assert is_bridge_permission_response({'behavior': 'allow'}) is True


def test_is_bridge_permission_response_accepts_deny() -> None:
    assert is_bridge_permission_response({'behavior': 'deny', 'message': 'no'}) is True


def test_is_bridge_permission_response_rejects_missing_behavior() -> None:
    assert is_bridge_permission_response({'message': 'no'}) is False


def test_is_bridge_permission_response_rejects_invalid_behavior() -> None:
    assert is_bridge_permission_response({'behavior': 'maybe'}) is False


def test_is_bridge_permission_response_rejects_non_dict() -> None:
    assert is_bridge_permission_response(None) is False
    assert is_bridge_permission_response('allow') is False
    assert is_bridge_permission_response([{'behavior': 'allow'}]) is False


def test_bridge_permission_response_typed_dict_usable() -> None:
    resp: BridgePermissionResponse = {'behavior': 'allow'}
    assert resp['behavior'] == 'allow'

    full: BridgePermissionResponse = {
        'behavior': 'allow',
        'updatedInput': {'foo': 1},
        'updatedPermissions': [{'rule': 'x'}],
        'message': 'ok',
    }
    assert full['updatedInput'] == {'foo': 1}


def test_protocol_accepts_duck_typed_impl() -> None:
    """BridgePermissionCallbacks is structural — any matching object works."""

    class _Fake:
        def send_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def send_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def cancel_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def on_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return lambda: None

    impl = _Fake()
    # All four methods must be present.
    for method in ('send_request', 'send_response', 'cancel_request', 'on_response'):
        assert hasattr(impl, method)
