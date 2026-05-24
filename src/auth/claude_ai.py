"""claude.ai OAuth token + entitlement helpers.

Ports the consumer-facing surface of ``typescript/src/utils/auth.ts``
that the bridge subsystem needs:

* ``get_claude_ai_oauth_tokens()`` — read the persisted OAuth token set.
* ``get_oauth_account_info()`` — read the persisted account profile (org
  UUID, plan, etc.).
* ``is_claude_ai_subscriber()`` — entitlement check used by
  ``is_bridge_enabled`` and ``getBridgeDisabledReason``.
* ``has_profile_scope()`` — token-scope check used by the same.
* ``check_and_refresh_oauth_token_if_needed()`` — proactive refresh
  called before every bridge API request.
* ``handle_oauth_401_error()`` — clear caches + force refresh after a
  server-reported 401.

Per refactoring plan §0.1 Q6: the TS file reads from a keychain-backed
secure storage that hasn't been ported to Python. Until Phase 10 lands
that storage layer, this module reads from environment variables as a
dev-override path:

* ``CLAUDE_AI_OAUTH_ACCESS_TOKEN`` — the OAuth access token.
* ``CLAUDE_AI_OAUTH_REFRESH_TOKEN`` — the refresh token.
* ``CLAUDE_AI_OAUTH_EXPIRES_AT`` — Unix-seconds expiry; defaults to
  ``0.0`` (unknown) when unset, which ``_is_near_or_past_expiry``
  treats as not-expiring (no refresh-warning fires for dev tokens).
* ``CLAUDE_AI_OAUTH_SCOPES`` — space-separated scope list.
* ``CLAUDE_AI_ORG_UUID`` — organization UUID.
* ``CLAUDE_AI_SUBSCRIBER`` — truthy/falsy override for the entitlement
  check (defaults: truthy when an access token is present).
* ``CLAUDE_AI_OAUTH_REFRESH_FAILED`` — set by test code to simulate a
  refresh failure.

This is intentionally similar to the existing ``bridge_config.py`` env-
override pattern (Phase 1). The public function signatures match what
Phase 3 (``bridgeApi``) and Phase 5 (``remoteBridgeCore``) need — so
swapping to a real keychain backend later is purely internal.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from src.auth.oauth import OAuthTokens

logger = logging.getLogger(__name__)


# Env-var keys (centralized so tests can patch them in one place).
ENV_ACCESS_TOKEN = 'CLAUDE_AI_OAUTH_ACCESS_TOKEN'
ENV_REFRESH_TOKEN = 'CLAUDE_AI_OAUTH_REFRESH_TOKEN'
ENV_EXPIRES_AT = 'CLAUDE_AI_OAUTH_EXPIRES_AT'
ENV_SCOPES = 'CLAUDE_AI_OAUTH_SCOPES'
ENV_ORG_UUID = 'CLAUDE_AI_ORG_UUID'
ENV_SUBSCRIBER_OVERRIDE = 'CLAUDE_AI_SUBSCRIBER'
ENV_REFRESH_FAILED = 'CLAUDE_AI_OAUTH_REFRESH_FAILED'


_PROFILE_SCOPE = 'user:profile'
_INFERENCE_SCOPE = 'user:inference'
_REFRESH_BUFFER_SECONDS = 60.0
"""Refresh tokens within this window of expiry rather than waiting until
they're stale. Matches TS ``checkAndRefreshOAuthTokenIfNeeded`` buffer.
"""


@dataclass(frozen=True)
class OAuthAccountInfo:
    """Persisted account profile.

    Mirrors the TS ``OauthAccount`` shape (only the fields the bridge
    needs). Phase 10 will populate this from ``/api/oauth/profile``;
    until then we read from env vars.
    """

    organization_uuid: str | None
    email_address: str | None = None
    is_subscriber: bool | None = None


def get_claude_ai_oauth_tokens() -> OAuthTokens | None:
    """Return the persisted claude.ai OAuth tokens, or ``None``.

    Mirrors TS ``getClaudeAIOAuthTokens`` consumer-facing semantics.
    Returns a plain ``OAuthTokens`` (no claude.ai-specific subclass —
    callers only need ``access_token`` / ``refresh_token`` /
    ``is_expired``).

    Env-var path until Phase 10 keychain lands. Returns ``None`` when
    no access token is set, matching TS "not logged in" semantics.
    """
    access_token = os.environ.get(ENV_ACCESS_TOKEN)
    if not access_token:
        return None
    refresh_token = os.environ.get(ENV_REFRESH_TOKEN, '')
    expires_at_raw = os.environ.get(ENV_EXPIRES_AT)
    if expires_at_raw:
        try:
            expires_at = float(expires_at_raw)
        except ValueError:
            expires_at = 0.0
    else:
        # No env override → "unknown" (matches absence of expiry on
        # imported tokens). ``_is_near_or_past_expiry`` treats 0.0 as
        # not-expiring, so dev tokens don't trigger refresh warnings.
        expires_at = 0.0
    scope = os.environ.get(ENV_SCOPES, '')
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type='Bearer',
        expires_at=expires_at,
        scope=scope,
    )


def get_oauth_account_info() -> OAuthAccountInfo | None:
    """Return the persisted claude.ai account profile, or ``None``.

    Mirrors TS ``getOauthAccountInfo`` consumer-facing semantics. Used
    by ``get_organization_uuid`` and the entitlement gate
    ``getBridgeDisabledReason`` flow.

    Env-var path until Phase 10. Returns ``None`` when no org UUID is
    set (TS returns ``undefined`` for unconfigured accounts).
    """
    org_uuid = os.environ.get(ENV_ORG_UUID)
    if not org_uuid:
        return None
    return OAuthAccountInfo(
        organization_uuid=org_uuid,
        email_address=None,
        is_subscriber=_subscriber_override(),
    )


def is_claude_ai_subscriber() -> bool:
    """Entitlement check used by ``is_bridge_enabled``.

    Mirrors TS ``isClaudeAISubscriber`` on ``utils/auth.ts:1585-1591``.
    The TS implementation is ``shouldUseClaudeAIAuth(getClaudeAIOAuthTokens()?.scopes)``,
    which checks for the ``user:inference`` scope. That scope's
    *practical* effect is to exclude Bedrock / Vertex / Console-only API
    keys (none of which receive ``user:inference``), but the *mechanism*
    is a positive scope check — replicate the mechanism, not the effect.

    Python port resolution order:

    * If ``CLAUDE_AI_SUBSCRIBER`` env-var override is set (test path),
      honor it unconditionally.
    * Otherwise: the persisted token must exist AND include the
      ``user:inference`` scope. Tokens lacking the scope (e.g.
      profile-only or api-only OAuth) return ``False`` — matches TS.
    """
    override = _subscriber_override()
    if override is not None:
        return override
    tokens = get_claude_ai_oauth_tokens()
    if tokens is None or not tokens.scope:
        return False
    return _INFERENCE_SCOPE in tokens.scope.split()


def has_profile_scope() -> bool:
    """Whether the persisted token includes the ``user:profile`` scope.

    Mirrors TS ``hasProfileScope``. Used by
    ``getBridgeDisabledReason`` to suggest re-login for tokens that
    can't populate ``oauthAccount.organizationUuid``.

    Env-var path: parses ``CLAUDE_AI_OAUTH_SCOPES``. Returns ``True``
    when the scope is present, ``False`` otherwise.
    """
    tokens = get_claude_ai_oauth_tokens()
    if tokens is None:
        return False
    if not tokens.scope:
        return False
    return _PROFILE_SCOPE in tokens.scope.split()


async def check_and_refresh_oauth_token_if_needed() -> None:
    """**No-op stub** — does NOT actually refresh tokens.

    Mirrors the *signature* of TS ``checkAndRefreshOAuthTokenIfNeeded``,
    but the *behavior* is intentionally a no-op until Phase 10 wires
    keychain-backed refresh. TS callers invoke this proactively before
    every bridge API call to avoid 401s; **Python callers will still get
    401s when a token approaches expiry** because nothing here refreshes.

    Emits a runtime warning when the persisted token is near or past
    expiry so the gap is visible in logs — a Phase 5+ porter writing
    against TS docs and expecting fresh tokens will see the warning
    instead of silently shipping a token-staleness bug.

    Errors are swallowed (matches TS best-effort behavior).
    """
    tokens = get_claude_ai_oauth_tokens()
    if tokens is None:
        return
    if not _is_near_or_past_expiry(tokens):
        return
    logger.warning(
        '[auth:claude_ai] token near/past expiry but '
        'check_and_refresh_oauth_token_if_needed is a no-op stub — '
        'Phase 10 keychain refresh not yet ported. 401 expected; '
        'handle_oauth_401_error is also a no-op (see its docstring).'
    )


async def handle_oauth_401_error(
    *, stale_token: str | None = None
) -> bool:
    """**No-op stub** — returns truthiness but does NOT refresh tokens.

    Mirrors the *signature* of TS ``handleOAuth401Error``, but the
    *behavior* is intentionally a no-op until Phase 10 wires keychain-
    backed refresh. Returns ``True`` when a token is persisted (caller
    will retry with the SAME token and likely get another 401) or
    ``False`` when no token / refresh-failure-override is set.

    The ``stale_token`` arg is accepted for forward compatibility —
    Phase 10 keychain integration will compare it against the current
    cached token to detect parallel refresh. The Python env-var path
    ignores it.

    Emits a runtime warning so Phase 5+ porters hitting a real 401 see
    explicitly that no refresh happened.
    """
    if os.environ.get(ENV_REFRESH_FAILED):
        return False
    has_token = get_claude_ai_oauth_tokens() is not None
    if has_token:
        logger.warning(
            '[auth:claude_ai] handle_oauth_401_error called but is a '
            'no-op stub — token not refreshed, caller will retry with '
            'the same token. Phase 10 keychain refresh not yet ported.'
        )
    return has_token


def _is_near_or_past_expiry(tokens: OAuthTokens) -> bool:
    """True when ``tokens`` expire within the refresh buffer."""
    if tokens.expires_at <= 0:
        return False  # Treat unknown-expiry as not-yet-expiring.
    return tokens.expires_at - time.time() <= _REFRESH_BUFFER_SECONDS


def _subscriber_override() -> bool | None:
    """Parse ``CLAUDE_AI_SUBSCRIBER`` env var: True / False / None."""
    raw = os.environ.get(ENV_SUBSCRIBER_OVERRIDE)
    if raw is None:
        return None
    return raw.lower() in ('1', 'true', 'yes', 'on')


__all__ = [
    'ENV_ACCESS_TOKEN',
    'ENV_EXPIRES_AT',
    'ENV_ORG_UUID',
    'ENV_REFRESH_FAILED',
    'ENV_REFRESH_TOKEN',
    'ENV_SCOPES',
    'ENV_SUBSCRIBER_OVERRIDE',
    'OAuthAccountInfo',
    'check_and_refresh_oauth_token_if_needed',
    'get_claude_ai_oauth_tokens',
    'get_oauth_account_info',
    'handle_oauth_401_error',
    'has_profile_scope',
    'is_claude_ai_subscriber',
]
