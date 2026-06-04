"""Tests for the router half of ``src.bridge.messaging``.

Covers ``handle_ingress_message`` routing, the type guards, the
forward-filter (``is_eligible_bridge_message``), ``extract_title_text``,
``normalize_control_message_keys``, ``make_result_message``, and the
``RemotePermissionResponse`` parser.
"""

from __future__ import annotations

import json

import pytest

from src.bridge.bounded_uuid_set import BoundedUUIDSet
from src.bridge.messaging import (
    AllowResponse,
    DenyResponse,
    extract_title_text,
    handle_ingress_message,
    is_eligible_bridge_message,
    is_sdk_control_request,
    is_sdk_control_response,
    is_sdk_message,
    make_result_message,
    normalize_control_message_keys,
    remote_permission_response_from_dict,
)


# ─── Type guards ─────────────────────────────────────────────────────────


class TestTypeGuards:
    def test_is_sdk_message_accepts_string_type(self) -> None:
        assert is_sdk_message({'type': 'user'})
        assert is_sdk_message({'type': 'assistant', 'extra': 'fields'})

    def test_is_sdk_message_rejects_missing_or_non_string_type(self) -> None:
        assert not is_sdk_message({})
        assert not is_sdk_message({'type': 123})
        assert not is_sdk_message('not a dict')
        assert not is_sdk_message(None)

    def test_is_sdk_control_response_requires_type_and_response(self) -> None:
        assert is_sdk_control_response({'type': 'control_response', 'response': {}})
        assert not is_sdk_control_response({'type': 'control_response'})  # no response
        assert not is_sdk_control_response({'type': 'control_request', 'response': {}})

    def test_is_sdk_control_request_requires_all_three_fields(self) -> None:
        assert is_sdk_control_request({
            'type': 'control_request', 'request_id': 'r1', 'request': {}
        })
        assert not is_sdk_control_request({'type': 'control_request', 'request_id': 'r1'})
        assert not is_sdk_control_request({'type': 'control_response', 'request_id': 'r1', 'request': {}})


# ─── normalize_control_message_keys ──────────────────────────────────────


class TestNormalizeControlMessageKeys:
    def test_known_camel_case_keys_get_snake_cased(self) -> None:
        out = normalize_control_message_keys({
            'requestId': 'r1', 'toolUseId': 'tu', 'workerEpoch': 5
        })
        assert out == {'request_id': 'r1', 'tool_use_id': 'tu', 'worker_epoch': 5}

    def test_snake_case_keys_pass_through(self) -> None:
        inp = {'request_id': 'r1', 'tool_use_id': 'tu'}
        assert normalize_control_message_keys(inp) == inp

    def test_unknown_camel_case_passes_through_with_debug_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Per gap analysis #24 + critic #20: unknown camelCase keys must
        NOT be silently dropped or guessed; pass through with debug log.
        """
        with caplog.at_level('DEBUG', logger='src.bridge.messaging'):
            out = normalize_control_message_keys({'futureField': 'val'})
        assert out == {'futureField': 'val'}
        assert any('Unknown camelCase key' in rec.message for rec in caplog.records)

    def test_recurses_into_nested_dicts(self) -> None:
        inp = {
            'controlRequest': {
                'requestId': 'r1',
                'request': {'subtype': 'set_model', 'model': 'opus'},
            }
        }
        out = normalize_control_message_keys(inp)
        assert out == {
            'control_request': {
                'request_id': 'r1',
                'request': {'subtype': 'set_model', 'model': 'opus'},
            }
        }

    def test_recurses_into_lists(self) -> None:
        inp = [{'requestId': 'r1'}, {'toolUseId': 'tu'}]
        out = normalize_control_message_keys(inp)
        assert out == [{'request_id': 'r1'}, {'tool_use_id': 'tu'}]

    def test_non_dict_non_list_passes_through(self) -> None:
        assert normalize_control_message_keys(42) == 42
        assert normalize_control_message_keys('hello') == 'hello'
        assert normalize_control_message_keys(None) is None


# ─── handle_ingress_message ──────────────────────────────────────────────


class TestHandleIngressMessage:
    def setup_method(self) -> None:
        self.posted = BoundedUUIDSet(100)
        self.inbound = BoundedUUIDSet(100)
        self.user_messages: list[dict] = []
        self.permission_responses: list[dict] = []
        self.control_requests: list[dict] = []

    def _route(self, payload: dict) -> None:
        handle_ingress_message(
            json.dumps(payload),
            self.posted,
            self.inbound,
            on_inbound_message=self.user_messages.append,
            on_permission_response=self.permission_responses.append,
            on_control_request=self.control_requests.append,
        )

    def test_user_message_forwarded_and_uuid_added_to_inbound_set(self) -> None:
        self._route({'type': 'user', 'uuid': 'u1', 'message': {'content': 'hi'}})
        assert len(self.user_messages) == 1
        assert self.inbound.has('u1')

    def test_assistant_message_dropped_on_read_side(self) -> None:
        """Only ``user`` messages are forwarded on the read side; others log+drop."""
        self._route({'type': 'assistant', 'uuid': 'a1'})
        assert self.user_messages == []

    def test_echo_dedup_ignores_own_posted_uuid(self) -> None:
        self.posted.add('u1')
        self._route({'type': 'user', 'uuid': 'u1', 'message': {}})
        assert self.user_messages == []

    def test_redelivery_dedup_ignores_inbound_uuid(self) -> None:
        self.inbound.add('u1')
        self._route({'type': 'user', 'uuid': 'u1', 'message': {}})
        assert self.user_messages == []

    def test_user_without_uuid_still_forwards(self) -> None:
        self._route({'type': 'user', 'message': {}})
        assert len(self.user_messages) == 1

    def test_control_response_routes_to_permission_handler(self) -> None:
        self._route({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'r1'}})
        assert len(self.permission_responses) == 1
        assert self.user_messages == []

    def test_control_request_routes_to_request_handler(self) -> None:
        self._route({
            'type': 'control_request',
            'request_id': 'r1',
            'request': {'subtype': 'initialize'},
        })
        assert len(self.control_requests) == 1
        assert self.user_messages == []

    def test_camel_case_request_id_normalized_before_routing(self) -> None:
        """Server may send camelCase; router should snake-case before dispatch."""
        self._route({
            'type': 'control_request',
            'requestId': 'r1',
            'request': {'subtype': 'initialize'},
        })
        assert len(self.control_requests) == 1
        # The dispatched payload has the normalized key.
        assert self.control_requests[0].get('request_id') == 'r1'

    def test_invalid_json_silently_dropped(self) -> None:
        handle_ingress_message(
            'not json {{{',
            self.posted,
            self.inbound,
            on_inbound_message=self.user_messages.append,
        )
        assert self.user_messages == []

    def test_non_object_json_silently_dropped(self) -> None:
        handle_ingress_message(
            '[1, 2, 3]',
            self.posted,
            self.inbound,
            on_inbound_message=self.user_messages.append,
        )
        assert self.user_messages == []

    def test_optional_callbacks_default_to_none(self) -> None:
        """Without callbacks, the router must not raise."""
        handle_ingress_message(
            json.dumps({'type': 'user', 'uuid': 'u1', 'message': {}}),
            self.posted,
            self.inbound,
        )  # no callbacks; should silently route + dedup


# ─── is_eligible_bridge_message ──────────────────────────────────────────


class TestIsEligibleBridgeMessage:
    def test_user_messages_forward(self) -> None:
        assert is_eligible_bridge_message({'type': 'user'})

    def test_assistant_messages_forward(self) -> None:
        assert is_eligible_bridge_message({'type': 'assistant'})

    def test_virtual_messages_filtered(self) -> None:
        assert not is_eligible_bridge_message({'type': 'user', 'isVirtual': True})
        assert not is_eligible_bridge_message({'type': 'assistant', 'isVirtual': True})

    def test_system_local_command_forwards(self) -> None:
        assert is_eligible_bridge_message({'type': 'system', 'subtype': 'local_command'})

    def test_system_other_subtype_filtered(self) -> None:
        assert not is_eligible_bridge_message({'type': 'system', 'subtype': 'init'})
        assert not is_eligible_bridge_message({'type': 'system', 'subtype': 'post_turn_summary'})

    def test_tool_result_filtered(self) -> None:
        assert not is_eligible_bridge_message({'type': 'tool_result'})

    def test_keep_alive_filtered(self) -> None:
        assert not is_eligible_bridge_message({'type': 'keep_alive'})


# ─── extract_title_text ──────────────────────────────────────────────────


class TestExtractTitleText:
    def test_user_text_string_content(self) -> None:
        msg = {'type': 'user', 'message': {'content': 'fix the bug'}}
        assert extract_title_text(msg) == 'fix the bug'

    def test_user_text_list_content_first_text_block(self) -> None:
        msg = {
            'type': 'user',
            'message': {'content': [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]},
        }
        assert extract_title_text(msg) == 'first'

    def test_non_user_returns_none(self) -> None:
        assert extract_title_text({'type': 'assistant', 'message': {'content': 'hi'}}) is None

    def test_meta_returns_none(self) -> None:
        assert extract_title_text({'type': 'user', 'isMeta': True, 'message': {'content': 'x'}}) is None

    def test_tool_use_result_returns_none(self) -> None:
        assert extract_title_text(
            {'type': 'user', 'toolUseResult': {}, 'message': {'content': 'x'}}
        ) is None

    def test_compact_summary_returns_none(self) -> None:
        assert extract_title_text(
            {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'x'}}
        ) is None

    def test_non_human_origin_returns_none(self) -> None:
        msg = {
            'type': 'user',
            'origin': {'kind': 'task_notification'},
            'message': {'content': 'x'},
        }
        assert extract_title_text(msg) is None

    def test_pure_display_tag_returns_none(self) -> None:
        msg = {
            'type': 'user',
            'message': {'content': '<ide_opened_file>foo.py</ide_opened_file>'},
        }
        assert extract_title_text(msg) is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_title_text({'type': 'user', 'message': {'content': ''}}) is None

    def test_jsx_uppercase_component_passes_through(self) -> None:
        """Per critic #1: TS regex is lowercase-only — `<Button>` is user prose."""
        msg = {
            'type': 'user',
            'message': {'content': 'fix the <Button> layout'},
        }
        assert extract_title_text(msg) == 'fix the <Button> layout'

    def test_doctype_html_passes_through(self) -> None:
        """`<!DOCTYPE html>` starts with `!`, not a letter — never stripped."""
        msg = {'type': 'user', 'message': {'content': 'remove <!DOCTYPE html>'}}
        assert extract_title_text(msg) == 'remove <!DOCTYPE html>'

    def test_mismatched_tags_pass_through(self) -> None:
        """`<foo>x</bar>` — no backreference match means TS leaves it alone."""
        msg = {'type': 'user', 'message': {'content': 'check <foo>x</bar>'}}
        assert extract_title_text(msg) == 'check <foo>x</bar>'

    def test_trailing_newline_stripped_after_tag(self) -> None:
        """The TS regex includes `\\n?` after the closing tag."""
        msg = {
            'type': 'user',
            'message': {'content': '<ide_opened_file>foo.py</ide_opened_file>\nactual prompt'},
        }
        assert extract_title_text(msg) == 'actual prompt'

    def test_multiline_tag_block_stripped(self) -> None:
        """`[\\s\\S]` in regex is JS equivalent of Python's re.DOTALL."""
        msg = {
            'type': 'user',
            'message': {
                'content': '<ide_selection>\nlines\nof\ncontent\n</ide_selection>real',
            },
        }
        assert extract_title_text(msg) == 'real'

    def test_unpaired_angle_brackets_pass_through(self) -> None:
        """User prose like 'when x < y' must not be eaten."""
        msg = {'type': 'user', 'message': {'content': 'verify when x < y'}}
        assert extract_title_text(msg) == 'verify when x < y'

    def test_tool_use_result_dict_disqualifies(self) -> None:
        """Per critic #20 + the `toolUseResult: {}` truthy fix."""
        msg = {'type': 'user', 'toolUseResult': {}, 'message': {'content': 'x'}}
        assert extract_title_text(msg) is None
        msg = {'type': 'user', 'toolUseResult': {'output': 'ok'}, 'message': {'content': 'x'}}
        assert extract_title_text(msg) is None

    def test_tool_use_result_none_does_not_disqualify(self) -> None:
        """`toolUseResult: None` (i.e., explicit null) means "no tool result"."""
        msg = {'type': 'user', 'toolUseResult': None, 'message': {'content': 'x'}}
        assert extract_title_text(msg) == 'x'


# ─── make_result_message ─────────────────────────────────────────────────


def test_make_result_message_has_required_fields_and_unique_uuid() -> None:
    r1 = make_result_message('cse_xyz')
    r2 = make_result_message('cse_xyz')
    assert r1['type'] == 'result'
    assert r1['subtype'] == 'success'
    assert r1['session_id'] == 'cse_xyz'
    assert r1['is_error'] is False
    assert r1['stop_reason'] is None
    assert r1['uuid'] != r2['uuid']  # generated fresh per call


# ─── RemotePermissionResponse parser ─────────────────────────────────────


class TestRemotePermissionResponse:
    def test_allow_with_snake_case(self) -> None:
        resp = remote_permission_response_from_dict({
            'behavior': 'allow',
            'updated_input': {'a': 1},
        })
        assert isinstance(resp, AllowResponse)
        assert resp.updated_input == {'a': 1}

    def test_allow_with_camel_case_compat(self) -> None:
        """The wire normalizer happens upstream, but if parser is called
        directly (Direct Connect) it should also accept ``updatedInput``.
        """
        resp = remote_permission_response_from_dict({
            'behavior': 'allow',
            'updatedInput': {'a': 1},
        })
        assert isinstance(resp, AllowResponse)
        assert resp.updated_input == {'a': 1}

    def test_allow_default_empty_input(self) -> None:
        resp = remote_permission_response_from_dict({'behavior': 'allow'})
        assert isinstance(resp, AllowResponse)
        assert resp.updated_input == {}

    def test_deny(self) -> None:
        resp = remote_permission_response_from_dict({
            'behavior': 'deny', 'message': 'too risky',
        })
        assert isinstance(resp, DenyResponse)
        assert resp.message == 'too risky'

    def test_unknown_behavior_raises(self) -> None:
        with pytest.raises(ValueError, match='unknown permission behavior'):
            remote_permission_response_from_dict({'behavior': 'maybe'})

    def test_allow_rejects_non_dict_updated_input(self) -> None:
        with pytest.raises(ValueError, match='updated_input must be dict'):
            remote_permission_response_from_dict({
                'behavior': 'allow', 'updated_input': 'not a dict',
            })

    def test_deny_rejects_non_string_message(self) -> None:
        with pytest.raises(ValueError, match='message must be a string'):
            remote_permission_response_from_dict({'behavior': 'deny', 'message': 42})
