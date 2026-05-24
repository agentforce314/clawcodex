"""Session-ingress auth token manager.

Ports ``typescript/src/utils/sessionIngressAuth.ts``.

Provides three primitives for the v2 bridge transport:

* ``get_session_ingress_auth_token()`` — read the current token from
  ``CLAUDE_CODE_SESSION_ACCESS_TOKEN`` env var. The TS implementation
  also supports two fallbacks that the Python port intentionally skips:

    * **File-descriptor fallback** (``CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR``)
      — needs ``fcntl`` + macOS/Linux ``/dev/fd`` vs ``/proc/self/fd``
      branching; only used by the legacy CCR codepath.
    * **Well-known file fallback** (``CLAUDE_SESSION_INGRESS_TOKEN_FILE``
      or ``/home/claude/.claude/remote/.session_ingress_token``) — the
      CCR subprocess hand-off path, not needed until CCR subprocess
      spawning is ported (out of Phase 2 scope).

  Both are deferred to a future phase that ports the CCR subprocess
  stack; Phase 5 (``remoteBridgeCore``) reads the env var directly.

* ``get_session_ingress_auth_headers()`` — build HTTP auth headers for
  the current token. Session keys (``sk-ant-sid-*``) get cookie auth +
  ``X-Organization-Uuid``; JWTs (everything else) get bearer auth.
* ``update_session_ingress_auth_token(token)`` — set the env var in-process
  for the next call (used by the REPL bridge to inject fresh JWTs after
  token refresh without restarting the process).

The CCRClient and SSETransport (``src/transports/``) already use
``get_session_ingress_auth_token``-equivalent code via their ``GetAuthHeaders``
callbacks. This module makes the canonical helper available to the
broader bridge subsystem (Phase 5+ orchestrators) so per-instance
callbacks aren't required for every call site.
"""

from __future__ import annotations

import os


ENV_VAR_TOKEN = 'CLAUDE_CODE_SESSION_ACCESS_TOKEN'
"""Env var holding the current session ingress token.

Mirrors TS ``CLAUDE_CODE_SESSION_ACCESS_TOKEN`` references throughout
``sessionIngressAuth.ts``.
"""

ENV_VAR_ORG_UUID = 'CLAUDE_CODE_ORGANIZATION_UUID'
"""Env var holding the org UUID for session-key auth.

Mirrors TS ``CLAUDE_CODE_ORGANIZATION_UUID`` on
``sessionIngressAuth.ts:124``.
"""

_SESSION_KEY_PREFIX = 'sk-ant-sid'


def get_session_ingress_auth_token() -> str | None:
    """Return the current session-ingress auth token, or ``None``.

    Mirrors the env-var portion of TS ``getSessionIngressAuthToken`` on
    ``sessionIngressAuth.ts:101-110``. The TS function also falls back
    to file-descriptor reading and a well-known file path; this Python
    port skips both because no v2 caller uses them (env-var-only path
    covers the bridge orchestrator and the REPL bridge transport refresh
    flow).
    """
    value = os.environ.get(ENV_VAR_TOKEN)
    return value or None


def get_session_ingress_auth_headers() -> dict[str, str]:
    """Build HTTP auth headers for the current session-ingress token.

    Mirrors TS ``getSessionIngressAuthHeaders`` on
    ``sessionIngressAuth.ts:117-131``.

    * Session keys (``sk-ant-sid-*``): cookie auth via ``sessionKey=…``,
      plus ``X-Organization-Uuid`` when ``CLAUDE_CODE_ORGANIZATION_UUID``
      is set.
    * JWTs (everything else): bearer auth via ``Authorization: Bearer …``.
    * No token: empty dict.
    """
    token = get_session_ingress_auth_token()
    if not token:
        return {}
    if token.startswith(_SESSION_KEY_PREFIX):
        headers = {'Cookie': f'sessionKey={token}'}
        org_uuid = os.environ.get(ENV_VAR_ORG_UUID)
        if org_uuid:
            headers['X-Organization-Uuid'] = org_uuid
        return headers
    return {'Authorization': f'Bearer {token}'}


def update_session_ingress_auth_token(token: str) -> None:
    """Update the session-ingress token in-process via the env var.

    Mirrors TS ``updateSessionIngressAuthToken`` on
    ``sessionIngressAuth.ts:138-140``. Used by the REPL bridge / Phase 5
    orchestrator to inject a fresh JWT after token refresh without
    restarting the process. Subsequent calls to
    ``get_session_ingress_auth_token`` / ``_headers`` see the new value.
    """
    os.environ[ENV_VAR_TOKEN] = token


__all__ = [
    'ENV_VAR_ORG_UUID',
    'ENV_VAR_TOKEN',
    'get_session_ingress_auth_headers',
    'get_session_ingress_auth_token',
    'update_session_ingress_auth_token',
]
