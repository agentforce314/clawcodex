"""Tests for ``src.auth.claude_ai``."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from src.auth.claude_ai import (
    ENV_ACCESS_TOKEN,
    ENV_EXPIRES_AT,
    ENV_ORG_UUID,
    ENV_REFRESH_FAILED,
    ENV_REFRESH_TOKEN,
    ENV_SCOPES,
    ENV_SUBSCRIBER_OVERRIDE,
    OAuthAccountInfo,
    check_and_refresh_oauth_token_if_needed,
    get_claude_ai_oauth_tokens,
    get_oauth_account_info,
    handle_oauth_401_error,
    has_profile_scope,
    is_claude_ai_subscriber,
)
from src.auth.oauth import OAuthTokens


def _no_claude_env() -> dict[str, str]:
    return {
        k: v for k, v in os.environ.items()
        if not k.startswith('CLAUDE_AI_')
    }


# ---------------------------------------------------------------------------
# get_claude_ai_oauth_tokens
# ---------------------------------------------------------------------------


def test_tokens_none_when_no_access_token() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert get_claude_ai_oauth_tokens() is None


def test_tokens_returns_oauth_tokens_with_access_token() -> None:
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok-abc'}
    with patch.dict(os.environ, env, clear=True):
        tokens = get_claude_ai_oauth_tokens()
    assert tokens is not None
    assert isinstance(tokens, OAuthTokens)
    assert tokens.access_token == 'tok-abc'
    assert tokens.refresh_token == ''
    assert tokens.token_type == 'Bearer'


def test_tokens_uses_explicit_refresh_token() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok-abc',
        ENV_REFRESH_TOKEN: 'ref-xyz',
    }
    with patch.dict(os.environ, env, clear=True):
        tokens = get_claude_ai_oauth_tokens()
    assert tokens is not None
    assert tokens.refresh_token == 'ref-xyz'


def test_tokens_parses_expires_at() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: '1800000000',
    }
    with patch.dict(os.environ, env, clear=True):
        tokens = get_claude_ai_oauth_tokens()
    assert tokens is not None
    assert tokens.expires_at == 1800000000.0


def test_tokens_falls_back_for_unparseable_expires() -> None:
    """Garbage in expires_at → 0.0 (treated as unknown)."""
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: 'not-a-number',
    }
    with patch.dict(os.environ, env, clear=True):
        tokens = get_claude_ai_oauth_tokens()
    assert tokens is not None
    assert tokens.expires_at == 0.0


def test_tokens_defaults_expires_at_to_zero_when_unset() -> None:
    """No expires_at env → 0.0 (unknown), matches absence-of-expiry on
    imported tokens. ``_is_near_or_past_expiry`` treats this as not
    expiring so refresh warnings don't fire on dev tokens.
    """
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        tokens = get_claude_ai_oauth_tokens()
    assert tokens is not None
    assert tokens.expires_at == 0.0


# ---------------------------------------------------------------------------
# get_oauth_account_info
# ---------------------------------------------------------------------------


def test_account_info_none_without_org_uuid() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert get_oauth_account_info() is None


def test_account_info_returns_org_uuid_when_set() -> None:
    env = _no_claude_env() | {ENV_ORG_UUID: 'org-123'}
    with patch.dict(os.environ, env, clear=True):
        info = get_oauth_account_info()
    assert isinstance(info, OAuthAccountInfo)
    assert info.organization_uuid == 'org-123'


def test_account_info_picks_up_subscriber_override() -> None:
    env = _no_claude_env() | {
        ENV_ORG_UUID: 'org-1',
        ENV_SUBSCRIBER_OVERRIDE: 'true',
    }
    with patch.dict(os.environ, env, clear=True):
        info = get_oauth_account_info()
    assert info is not None
    assert info.is_subscriber is True


# ---------------------------------------------------------------------------
# is_claude_ai_subscriber
# ---------------------------------------------------------------------------


def test_subscriber_false_when_no_token_and_no_override() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert is_claude_ai_subscriber() is False


def test_subscriber_false_when_token_lacks_inference_scope() -> None:
    """Per TS: needs ``user:inference`` scope, not just any token."""
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        assert is_claude_ai_subscriber() is False


def test_subscriber_false_when_only_other_scopes() -> None:
    """``api`` or ``user:profile`` alone do not grant subscriber status."""
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'api user:profile',
    }
    with patch.dict(os.environ, env, clear=True):
        assert is_claude_ai_subscriber() is False


def test_subscriber_true_when_inference_scope_present() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'api user:inference',
    }
    with patch.dict(os.environ, env, clear=True):
        assert is_claude_ai_subscriber() is True


def test_subscriber_override_false_wins_over_token() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'user:inference',
        ENV_SUBSCRIBER_OVERRIDE: 'false',
    }
    with patch.dict(os.environ, env, clear=True):
        assert is_claude_ai_subscriber() is False


def test_subscriber_override_true_wins_over_no_token() -> None:
    env = _no_claude_env() | {ENV_SUBSCRIBER_OVERRIDE: '1'}
    with patch.dict(os.environ, env, clear=True):
        assert is_claude_ai_subscriber() is True


# ---------------------------------------------------------------------------
# has_profile_scope
# ---------------------------------------------------------------------------


def test_profile_scope_false_when_no_token() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert has_profile_scope() is False


def test_profile_scope_false_when_scope_unset() -> None:
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        assert has_profile_scope() is False


def test_profile_scope_true_when_present() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'api user:profile',
    }
    with patch.dict(os.environ, env, clear=True):
        assert has_profile_scope() is True


def test_profile_scope_false_when_other_scopes() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'api',
    }
    with patch.dict(os.environ, env, clear=True):
        assert has_profile_scope() is False


def test_profile_scope_does_not_partial_match() -> None:
    """``user:profile`` is required as a whole token, not as a substring."""
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_SCOPES: 'api user:profile_other',
    }
    with patch.dict(os.environ, env, clear=True):
        assert has_profile_scope() is False


# ---------------------------------------------------------------------------
# check_and_refresh_oauth_token_if_needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_noop_when_no_token() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        # Should not raise.
        await check_and_refresh_oauth_token_if_needed()


@pytest.mark.asyncio
async def test_refresh_noop_when_token_not_near_expiry() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: str(time.time() + 3600),
    }
    with patch.dict(os.environ, env, clear=True):
        await check_and_refresh_oauth_token_if_needed()


@pytest.mark.asyncio
async def test_refresh_noop_when_expires_unknown() -> None:
    """expires_at=0 → unknown — don't refresh (matches TS)."""
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: '0',
    }
    with patch.dict(os.environ, env, clear=True):
        await check_and_refresh_oauth_token_if_needed()


# ---------------------------------------------------------------------------
# handle_oauth_401_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_401_returns_true_when_token_present() -> None:
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        assert await handle_oauth_401_error() is True


@pytest.mark.asyncio
async def test_handle_401_returns_false_when_no_token() -> None:
    with patch.dict(os.environ, _no_claude_env(), clear=True):
        assert await handle_oauth_401_error() is False


@pytest.mark.asyncio
async def test_handle_401_returns_false_when_refresh_failure_set() -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_REFRESH_FAILED: '1',
    }
    with patch.dict(os.environ, env, clear=True):
        assert await handle_oauth_401_error() is False


@pytest.mark.asyncio
async def test_handle_401_accepts_stale_token_kwarg() -> None:
    """The ``stale_token`` arg is accepted (Phase 10 will use it)."""
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        assert await handle_oauth_401_error(stale_token='old') is True


# ---------------------------------------------------------------------------
# No-op stub warnings (CRITIC follow-up: make silent staleness loud)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_refresh_warns_when_token_near_expiry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A near-expiry token should emit a WARNING that refresh is a no-op."""
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: str(time.time() + 10),  # 10s out — within buffer
    }
    with patch.dict(os.environ, env, clear=True):
        with caplog.at_level('WARNING', logger='src.auth.claude_ai'):
            await check_and_refresh_oauth_token_if_needed()
    assert any('no-op stub' in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_check_and_refresh_no_warning_when_token_fresh(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = _no_claude_env() | {
        ENV_ACCESS_TOKEN: 'tok',
        ENV_EXPIRES_AT: str(time.time() + 3600),
    }
    with patch.dict(os.environ, env, clear=True):
        with caplog.at_level('WARNING', logger='src.auth.claude_ai'):
            await check_and_refresh_oauth_token_if_needed()
    assert not any('no-op stub' in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_handle_401_warns_when_token_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``handle_oauth_401_error`` must warn that refresh is a no-op."""
    env = _no_claude_env() | {ENV_ACCESS_TOKEN: 'tok'}
    with patch.dict(os.environ, env, clear=True):
        with caplog.at_level('WARNING', logger='src.auth.claude_ai'):
            await handle_oauth_401_error()
    assert any('no-op stub' in rec.message for rec in caplog.records)
