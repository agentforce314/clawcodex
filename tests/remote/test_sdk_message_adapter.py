"""Tests for ``src.remote.sdk_message_adapter``."""

from __future__ import annotations

import pytest

from src.remote.sdk_message_adapter import (
    adapt_permission_response,
    adapt_sdk_to_wire_user_message,
    adapt_wire_to_sdk,
)


class TestAdaptWireToSdk:
    def test_normalizes_camel_case_keys(self) -> None:
        out = adapt_wire_to_sdk({
            'type': 'control_request',
            'requestId': 'r1',
            'request': {'subtype': 'set_model', 'model': 'opus'},
        })
        assert out == {
            'type': 'control_request',
            'request_id': 'r1',
            'request': {'subtype': 'set_model', 'model': 'opus'},
        }

    def test_passes_through_snake_case(self) -> None:
        inp = {'type': 'user', 'message': {'content': 'hi'}}
        assert adapt_wire_to_sdk(inp) == inp


class TestAdaptSdkToWireUserMessage:
    def test_minimal(self) -> None:
        env = adapt_sdk_to_wire_user_message('hello', session_id='cse_x')
        assert env == {
            'type': 'user',
            'message': {'role': 'user', 'content': 'hello'},
            'parent_tool_use_id': None,
            'session_id': 'cse_x',
        }

    def test_with_uuid(self) -> None:
        env = adapt_sdk_to_wire_user_message('hi', session_id='s', uuid='u1')
        assert env['uuid'] == 'u1'

    def test_with_parent_tool_use_id(self) -> None:
        env = adapt_sdk_to_wire_user_message(
            'sub-prompt', session_id='s', parent_tool_use_id='tu1',
        )
        assert env['parent_tool_use_id'] == 'tu1'

    def test_list_content_passed_through(self) -> None:
        content = [{'type': 'text', 'text': 'hi'}]
        env = adapt_sdk_to_wire_user_message(content, session_id='s')
        assert env['message']['content'] == content


class TestAdaptPermissionResponse:
    def test_allow_with_updated_input(self) -> None:
        env = adapt_permission_response(
            'r1', 'allow', updated_input={'command': 'ls -la'},
        )
        assert env['type'] == 'control_response'
        assert env['response']['subtype'] == 'success'
        assert env['response']['request_id'] == 'r1'
        assert env['response']['response']['behavior'] == 'allow'
        assert env['response']['response']['updatedInput'] == {'command': 'ls -la'}

    def test_allow_default_empty_input(self) -> None:
        env = adapt_permission_response('r1', 'allow')
        assert env['response']['response']['updatedInput'] == {}

    def test_deny_with_message(self) -> None:
        env = adapt_permission_response('r1', 'deny', message='too risky')
        assert env['response']['response']['behavior'] == 'deny'
        assert env['response']['response']['message'] == 'too risky'

    def test_deny_default_empty_message(self) -> None:
        env = adapt_permission_response('r1', 'deny')
        assert env['response']['response']['message'] == ''

    def test_unknown_behavior_raises(self) -> None:
        with pytest.raises(ValueError, match='unknown permission behavior'):
            adapt_permission_response('r1', 'maybe')
