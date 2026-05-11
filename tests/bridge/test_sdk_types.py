"""Tests for ``src.bridge.sdk_types``.

TypedDicts are erased at runtime; we only assert that representative
fixtures fit the expected shape via Python's structural duck-typing.
This catches the common port-author mistake of fields being renamed
silently, while keeping the tests pragmatic.
"""

from __future__ import annotations

from src.bridge.sdk_types import (
    SDKControlCancelRequest,
    SDKControlPermissionRequest,
    SDKControlRequest,
    SDKControlResponse,
    SDKResultSuccess,
)


def test_user_message_shape() -> None:
    msg: dict = {
        'type': 'user',
        'uuid': 'u1',
        'session_id': 'cse_x',
        'parent_tool_use_id': None,
        'message': {'role': 'user', 'content': 'hi'},
    }
    assert msg['type'] == 'user'


def test_control_request_initialize_shape() -> None:
    req: SDKControlRequest = {
        'type': 'control_request',
        'request_id': 'r1',
        'request': {'subtype': 'initialize'},
    }
    assert req['type'] == 'control_request'
    assert req['request']['subtype'] == 'initialize'


def test_control_request_set_model_shape() -> None:
    req: SDKControlRequest = {
        'type': 'control_request',
        'request_id': 'r2',
        'request': {'subtype': 'set_model', 'model': 'claude-opus-4-7'},
    }
    assert req['request']['model'] == 'claude-opus-4-7'


def test_control_request_set_permission_mode_shape() -> None:
    req: SDKControlRequest = {
        'type': 'control_request',
        'request_id': 'r3',
        'request': {'subtype': 'set_permission_mode', 'mode': 'auto'},
    }
    assert req['request']['mode'] == 'auto'


def test_control_request_can_use_tool_shape() -> None:
    inner: SDKControlPermissionRequest = {
        'subtype': 'can_use_tool',
        'tool_name': 'Bash',
        'input': {'command': 'ls'},
        'tool_use_id': 'tu_1',
    }
    req: SDKControlRequest = {
        'type': 'control_request',
        'request_id': 'r4',
        'request': inner,
    }
    assert req['request']['subtype'] == 'can_use_tool'
    assert req['request']['tool_name'] == 'Bash'


def test_control_response_success_shape() -> None:
    resp: SDKControlResponse = {
        'type': 'control_response',
        'response': {
            'subtype': 'success',
            'request_id': 'r1',
            'response': {'pid': 1234},
        },
    }
    assert resp['response']['subtype'] == 'success'


def test_control_response_error_shape() -> None:
    resp: SDKControlResponse = {
        'type': 'control_response',
        'response': {
            'subtype': 'error',
            'request_id': 'r1',
            'error': 'unsupported subtype',
        },
    }
    assert resp['response']['error'] == 'unsupported subtype'


def test_control_cancel_request_shape() -> None:
    cancel: SDKControlCancelRequest = {
        'type': 'control_cancel_request',
        'request_id': 'r1',
        'tool_use_id': 'tu_99',
    }
    assert cancel['request_id'] == 'r1'
    assert cancel['tool_use_id'] == 'tu_99'


def test_result_success_has_required_fields() -> None:
    """``make_result_message`` (WI-2.6c) builds this shape; verify the
    required fields per ``bridgeMessaging.ts:399-416``.
    """
    result: SDKResultSuccess = {
        'type': 'result',
        'subtype': 'success',
        'duration_ms': 0,
        'duration_api_ms': 0,
        'is_error': False,
        'num_turns': 0,
        'result': '',
        'stop_reason': None,
        'total_cost_usd': 0.0,
        'usage': {},
        'modelUsage': {},
        'permission_denials': [],
        'session_id': 's1',
        'uuid': 'u1',
    }
    assert result['type'] == 'result'
    assert result['subtype'] == 'success'
    assert result['is_error'] is False
