"""Tests for ``src.bridge.work_secret``."""

from __future__ import annotations

import base64
import json

import pytest

from src.bridge.work_secret import (
    build_ccr_v2_sdk_url,
    build_sdk_url,
    decode_work_secret,
    same_session_id,
)


def _encode_secret(payload: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).rstrip(b'=').decode('ascii')


def test_decode_minimal_v1_secret() -> None:
    secret = _encode_secret({
        'version': 1,
        'session_ingress_token': 'tok-123',
        'api_base_url': 'https://api.anthropic.com',
    })
    parsed = decode_work_secret(secret)
    assert parsed.version == 1
    assert parsed.session_ingress_token == 'tok-123'
    assert parsed.api_base_url == 'https://api.anthropic.com'
    assert parsed.sources == ()
    assert parsed.environment_variables is None


def test_decode_secret_with_all_optional_fields() -> None:
    secret = _encode_secret({
        'version': 1,
        'session_ingress_token': 'tok',
        'api_base_url': 'http://localhost:8000',
        'sources': [{'type': 'git_repository'}],
        'auth': [{'type': 'oauth', 'token': 'oauth-tok'}],
        'claude_code_args': {'flag': 'value'},
        'mcp_config': {'mcpServers': {}},
        'environment_variables': {'KEY': 'val'},
        'use_code_sessions': True,
    })
    parsed = decode_work_secret(secret)
    assert parsed.sources == ({'type': 'git_repository'},)
    assert parsed.use_code_sessions is True
    assert parsed.environment_variables == {'KEY': 'val'}


def test_decode_rejects_non_v1_version() -> None:
    secret = _encode_secret({
        'version': 2,
        'session_ingress_token': 'tok',
        'api_base_url': 'https://x',
    })
    with pytest.raises(ValueError, match='Unsupported work secret version'):
        decode_work_secret(secret)


def test_decode_rejects_missing_token() -> None:
    secret = _encode_secret({
        'version': 1,
        'api_base_url': 'https://x',
    })
    with pytest.raises(ValueError, match='session_ingress_token'):
        decode_work_secret(secret)


def test_decode_rejects_empty_token() -> None:
    secret = _encode_secret({
        'version': 1,
        'session_ingress_token': '',
        'api_base_url': 'https://x',
    })
    with pytest.raises(ValueError, match='session_ingress_token'):
        decode_work_secret(secret)


def test_decode_rejects_missing_base_url() -> None:
    secret = _encode_secret({
        'version': 1,
        'session_ingress_token': 'tok',
    })
    with pytest.raises(ValueError, match='api_base_url'):
        decode_work_secret(secret)


def test_decode_rejects_invalid_base64() -> None:
    with pytest.raises(ValueError, match='base64url'):
        decode_work_secret('!!! not base64 !!!')


def test_decode_rejects_non_object_json() -> None:
    secret = base64.urlsafe_b64encode(b'[1,2,3]').rstrip(b'=').decode('ascii')
    with pytest.raises(ValueError, match='must be a JSON object'):
        decode_work_secret(secret)


def test_build_sdk_url_localhost_uses_v2_ws() -> None:
    url = build_sdk_url('http://localhost:8000', 'cse_abc')
    assert url == 'ws://localhost:8000/v2/session_ingress/ws/cse_abc'


def test_build_sdk_url_127_0_0_1_uses_v2_ws() -> None:
    url = build_sdk_url('http://127.0.0.1:8000', 'cse_abc')
    assert url == 'ws://127.0.0.1:8000/v2/session_ingress/ws/cse_abc'


def test_build_sdk_url_production_uses_v1_wss() -> None:
    url = build_sdk_url('https://api.anthropic.com', 'cse_abc')
    assert url == 'wss://api.anthropic.com/v1/session_ingress/ws/cse_abc'


def test_build_sdk_url_strips_trailing_slash() -> None:
    url = build_sdk_url('https://api.anthropic.com/', 'cse_abc')
    assert url == 'wss://api.anthropic.com/v1/session_ingress/ws/cse_abc'


def test_build_ccr_v2_sdk_url_strips_trailing_slash() -> None:
    assert build_ccr_v2_sdk_url('https://api.anthropic.com', 'cse_xyz') == (
        'https://api.anthropic.com/v1/code/sessions/cse_xyz'
    )
    assert build_ccr_v2_sdk_url('https://api.anthropic.com/', 'cse_xyz') == (
        'https://api.anthropic.com/v1/code/sessions/cse_xyz'
    )


def test_same_session_id_identical_strings() -> None:
    assert same_session_id('cse_abc1234', 'cse_abc1234')


def test_same_session_id_cse_vs_session_prefix() -> None:
    """``cse_*`` (infra) and ``session_*`` (compat) share the same UUID body."""
    assert same_session_id('cse_abc1234', 'session_abc1234')
    assert same_session_id('session_abc1234', 'cse_abc1234')


def test_same_session_id_session_staging_prefix() -> None:
    """``session_staging_*`` is the staging-environment compat form."""
    assert same_session_id('cse_abc1234', 'session_staging_abc1234')


def test_same_session_id_different_uuids_returns_false() -> None:
    assert not same_session_id('cse_abc1234', 'cse_xyz5678')
    assert not same_session_id('cse_abc1234', 'session_xyz5678')


def test_same_session_id_short_body_returns_false() -> None:
    """Body shorter than 4 chars must not false-match."""
    assert not same_session_id('a_b', 'c_b')  # body 'b' is too short
