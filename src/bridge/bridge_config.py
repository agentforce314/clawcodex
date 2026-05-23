"""Shared bridge auth/URL resolution.

Ports ``typescript/src/bridge/bridgeConfig.ts``.

Consolidates the ``CLAUDE_BRIDGE_*`` internal-only dev overrides that were
previously copy-pasted across the bridge subsystem. Two layers:

* ``*_override()`` returns the env var if set, else ``None``.
* The non-``_override`` wrapper falls through to the real OAuth store /
  config.

Per refactoring plan §0.1 Q6 + §2 item 9: the OAuth fallthrough (TS
``getClaudeAIOAuthTokens()?.accessToken``) is **not yet ported in the
Python build**. ``get_bridge_access_token`` returns ``None`` on
fallthrough (callers treat as "not logged in"). ``get_bridge_base_url``
returns ``_DEFAULT_PRODUCTION_BASE_URL`` (matches TS production
``getOauthConfig().BASE_API_URL`` after the GA OAuth config was finalized
— Anthropic's production API host). When Phase 2 lands the real OAuth
helpers, the fallthroughs will switch to the keychain reader; callers
see no API change.
"""

from __future__ import annotations

import os

_DEFAULT_PRODUCTION_BASE_URL = 'https://api.anthropic.com'
"""Fallback when no override AND no Phase 2 OAuth config is available.

Mirrors TS ``getOauthConfig().BASE_API_URL`` for the production build. The
TS function reads from an OAuth config object populated at startup; the
Python port lands the constant inline until that config layer is ported.
"""


def get_bridge_token_override() -> str | None:
    """Dev override: ``CLAUDE_BRIDGE_OAUTH_TOKEN``, else ``None``.

    Mirrors TS ``getBridgeTokenOverride`` on ``bridgeConfig.ts:18-20``.
    Empty string is treated as unset (matches the TS ``|| undefined``).
    """
    value = os.environ.get('CLAUDE_BRIDGE_OAUTH_TOKEN')
    return value or None


def get_bridge_base_url_override() -> str | None:
    """Dev override: ``CLAUDE_BRIDGE_BASE_URL``, else ``None``.

    Mirrors TS ``getBridgeBaseUrlOverride`` on ``bridgeConfig.ts:23-25``.
    """
    value = os.environ.get('CLAUDE_BRIDGE_BASE_URL')
    return value or None


def get_bridge_access_token() -> str | None:
    """Access token for bridge API calls.

    Mirrors TS ``getBridgeAccessToken`` on ``bridgeConfig.ts:31-33``.
    Returns the dev override if set, else ``None`` (Phase 2 will swap
    this fallthrough for the OAuth keychain read).
    """
    return get_bridge_token_override()


def get_bridge_base_url() -> str:
    """Base URL for bridge API calls.

    Mirrors TS ``getBridgeBaseUrl`` on ``bridgeConfig.ts:39-41``. Always
    returns a non-``None`` URL. Returns the dev override if set, else the
    production base URL constant.
    """
    return get_bridge_base_url_override() or _DEFAULT_PRODUCTION_BASE_URL


__all__ = [
    'get_bridge_access_token',
    'get_bridge_base_url',
    'get_bridge_base_url_override',
    'get_bridge_token_override',
]
