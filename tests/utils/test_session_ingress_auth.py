"""Tests for ``src.utils.session_ingress_auth``."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.utils.session_ingress_auth import (
    ENV_VAR_ORG_UUID,
    ENV_VAR_TOKEN,
    get_session_ingress_auth_headers,
    get_session_ingress_auth_token,
    update_session_ingress_auth_token,
)


def _no_si_env() -> dict[str, str]:
    """Env without the session-ingress vars."""
    return {
        k: v for k, v in os.environ.items()
        if k not in (ENV_VAR_TOKEN, ENV_VAR_ORG_UUID)
    }


def test_get_token_returns_env_value() -> None:
    env = _no_si_env() | {ENV_VAR_TOKEN: 'jwt-abc'}
    with patch.dict(os.environ, env, clear=True):
        assert get_session_ingress_auth_token() == 'jwt-abc'


def test_get_token_returns_none_when_unset() -> None:
    with patch.dict(os.environ, _no_si_env(), clear=True):
        assert get_session_ingress_auth_token() is None


def test_get_token_returns_none_when_empty_string() -> None:
    """Empty string treated as unset (matches TS truthy check)."""
    env = _no_si_env() | {ENV_VAR_TOKEN: ''}
    with patch.dict(os.environ, env, clear=True):
        assert get_session_ingress_auth_token() is None


def test_headers_empty_when_no_token() -> None:
    with patch.dict(os.environ, _no_si_env(), clear=True):
        assert get_session_ingress_auth_headers() == {}


def test_headers_bearer_for_jwt_token() -> None:
    """JWTs (anything not ``sk-ant-sid-*``) use bearer auth."""
    env = _no_si_env() | {ENV_VAR_TOKEN: 'eyJhbGc.payload.sig'}
    with patch.dict(os.environ, env, clear=True):
        h = get_session_ingress_auth_headers()
    assert h == {'Authorization': 'Bearer eyJhbGc.payload.sig'}


def test_headers_cookie_for_session_key() -> None:
    """``sk-ant-sid-*`` tokens use cookie auth, no Authorization header."""
    env = _no_si_env() | {ENV_VAR_TOKEN: 'sk-ant-sid-abc'}
    with patch.dict(os.environ, env, clear=True):
        h = get_session_ingress_auth_headers()
    assert h == {'Cookie': 'sessionKey=sk-ant-sid-abc'}


def test_headers_cookie_includes_org_uuid_when_set() -> None:
    """Session-key auth pulls ``X-Organization-Uuid`` from env."""
    env = _no_si_env() | {
        ENV_VAR_TOKEN: 'sk-ant-sid-abc',
        ENV_VAR_ORG_UUID: 'org-123',
    }
    with patch.dict(os.environ, env, clear=True):
        h = get_session_ingress_auth_headers()
    assert h == {
        'Cookie': 'sessionKey=sk-ant-sid-abc',
        'X-Organization-Uuid': 'org-123',
    }


def test_headers_cookie_omits_org_uuid_when_unset() -> None:
    env = _no_si_env() | {ENV_VAR_TOKEN: 'sk-ant-sid-abc'}
    with patch.dict(os.environ, env, clear=True):
        h = get_session_ingress_auth_headers()
    assert 'X-Organization-Uuid' not in h


def test_update_token_sets_env_var_for_subsequent_calls() -> None:
    with patch.dict(os.environ, _no_si_env(), clear=True):
        assert get_session_ingress_auth_token() is None
        update_session_ingress_auth_token('jwt-new')
        assert get_session_ingress_auth_token() == 'jwt-new'
        # Headers reflect the new token immediately.
        assert get_session_ingress_auth_headers() == {
            'Authorization': 'Bearer jwt-new'
        }


def test_update_token_overwrites_previous_value() -> None:
    env = _no_si_env() | {ENV_VAR_TOKEN: 'jwt-old'}
    with patch.dict(os.environ, env, clear=True):
        update_session_ingress_auth_token('jwt-new')
        assert get_session_ingress_auth_token() == 'jwt-new'
