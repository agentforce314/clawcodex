"""Tests for ``src.bridge.debug_utils``."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from src.bridge.debug_utils import (
    DEBUG_MSG_LIMIT,
    debug_body,
    debug_truncate,
    describe_http_error,
    extract_error_detail,
    extract_http_status,
    log_bridge_skip,
    redact_secrets,
)


def test_redact_secrets_short_token_fully_redacted() -> None:
    """Short tokens (<16 chars) become ``[REDACTED]``."""
    s = '{"access_token":"shortone"}'
    assert redact_secrets(s) == '{"access_token":"[REDACTED]"}'


def test_redact_secrets_long_token_partial() -> None:
    """Long tokens keep first 8 + last 4 chars."""
    token = 'abcd1234EFGH5678ZZZZ'  # 20 chars >= 16
    s = f'{{"session_ingress_token":"{token}"}}'
    out = redact_secrets(s)
    assert 'abcd1234...ZZZZ' in out
    # The middle (1234EFGH5678) must not appear.
    assert 'EFGH5678' not in out
    assert '"session_ingress_token":' in out


def test_redact_secrets_covers_all_field_names() -> None:
    fields = ['session_ingress_token', 'environment_secret', 'access_token', 'secret', 'token']
    for field in fields:
        s = f'{{"{field}":"verylongtokenvaluexxx"}}'
        out = redact_secrets(s)
        assert 'verylongtoken' not in out, f'field {field} not redacted'


def test_redact_secrets_leaves_unrelated_fields_alone() -> None:
    s = '{"user_id":"u-123","access_token":"verylongtokenxxxxxx"}'
    out = redact_secrets(s)
    assert '"user_id":"u-123"' in out
    assert 'verylongtoken' not in out


def test_debug_truncate_short_string_unchanged() -> None:
    assert debug_truncate('hello') == 'hello'


def test_debug_truncate_collapses_newlines() -> None:
    assert debug_truncate('a\nb\nc') == 'a\\nb\\nc'


def test_debug_truncate_long_string_truncated() -> None:
    s = 'x' * (DEBUG_MSG_LIMIT + 100)
    out = debug_truncate(s)
    assert out.startswith('x' * 200)
    assert f'({len(s)} chars)' in out
    assert len(out) < len(s)


def test_debug_body_dict_serialized_and_redacted() -> None:
    payload = {'access_token': 'verylongtokenxxxxx', 'msg': 'hi'}
    out = debug_body(payload)
    assert 'verylongtoken' not in out
    assert 'hi' in out


def test_debug_body_string_passes_through_with_redaction() -> None:
    out = debug_body('{"token":"verylongtokenxxxxx"}')
    assert 'verylongtoken' not in out


def test_extract_http_status_with_response_attr() -> None:
    err = SimpleNamespace(response=SimpleNamespace(status_code=404))
    assert extract_http_status(err) == 404


def test_extract_http_status_no_response_returns_none() -> None:
    err = Exception('plain error')
    assert extract_http_status(err) is None


def test_extract_error_detail_finds_message_field() -> None:
    assert extract_error_detail({'message': 'bad'}) == 'bad'


def test_extract_error_detail_finds_nested_error_message() -> None:
    assert extract_error_detail({'error': {'message': 'denied'}}) == 'denied'


def test_extract_error_detail_returns_none_for_missing() -> None:
    assert extract_error_detail({'unrelated': 'data'}) is None
    assert extract_error_detail('not a dict') is None
    assert extract_error_detail(None) is None


def test_describe_http_error_appends_detail() -> None:
    response = SimpleNamespace(json=lambda: {'message': 'unauthorized'})
    err = SimpleNamespace(response=response, __str__=lambda self: 'HTTP 401')
    # SimpleNamespace doesn't accept __str__ override above; build manually.

    class _Err(Exception):
        def __init__(self) -> None:
            super().__init__('HTTP 401')
            self.response = response

    out = describe_http_error(_Err())
    assert 'HTTP 401' in out
    assert 'unauthorized' in out


def test_describe_http_error_no_response_returns_str() -> None:
    err = ValueError('plain')
    assert describe_http_error(err) == 'plain'


def test_log_bridge_skip_emits_info_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger='src.bridge.debug_utils'):
        log_bridge_skip('no_token', debug_msg='cache miss', v2=True)
    assert any('no_token' in rec.message for rec in caplog.records)
