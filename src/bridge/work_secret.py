"""WorkSecret decoder + URL builders + tagged-session-ID compat helpers.

Ports ``typescript/src/bridge/workSecret.ts`` (the wire-format primitive
half — ``decode_work_secret``, ``build_sdk_url``, ``same_session_id``,
``build_ccr_v2_sdk_url``). The ``register_worker`` HTTP call lives in
``src/bridge/code_session_api.py`` (Phase 3 WI-3.4) — different concern
(HTTP), grouped with other CCR v2 API calls.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class WorkSecret:
    """Parsed work-secret payload (Bridge v1).

    Schema mirrors ``typescript/src/bridge/types.ts:33-51``. The ``version``
    field MUST be 1; ``decode_work_secret`` raises if not.
    """

    version: int
    session_ingress_token: str
    api_base_url: str
    sources: tuple[dict[str, Any], ...] = ()
    auth: tuple[dict[str, Any], ...] = ()
    claude_code_args: dict[str, str] | None = None
    mcp_config: object | None = None
    environment_variables: dict[str, str] | None = None
    use_code_sessions: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def decode_work_secret(secret: str) -> WorkSecret:
    """Decode a base64url-encoded work secret JSON.

    Raises ``ValueError`` on:
      - Non-base64url payload.
      - Non-object JSON.
      - Missing or non-1 ``version`` field.
      - Missing/empty ``session_ingress_token``.
      - Missing/non-string ``api_base_url``.

    Mirrors ``typescript/src/bridge/workSecret.ts:6-32``.
    """
    try:
        # Pad to a multiple of 4 — base64url omits padding by convention.
        padding_needed = (-len(secret)) % 4
        decoded_bytes = base64.urlsafe_b64decode(secret + '=' * padding_needed)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'work secret is not valid base64url: {exc}') from exc

    try:
        parsed: Any = json.loads(decoded_bytes.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'work secret payload is not valid JSON: {exc}') from exc

    if not isinstance(parsed, dict):
        raise ValueError(f'work secret must be a JSON object, got {type(parsed).__name__}')

    version = parsed.get('version')
    if version != 1:
        raise ValueError(f'Unsupported work secret version: {version!r}')

    token = parsed.get('session_ingress_token')
    if not isinstance(token, str) or not token:
        raise ValueError('Invalid work secret: missing or empty session_ingress_token')

    base_url = parsed.get('api_base_url')
    if not isinstance(base_url, str):
        raise ValueError('Invalid work secret: missing api_base_url')

    sources_raw = parsed.get('sources', [])
    auth_raw = parsed.get('auth', [])

    return WorkSecret(
        version=version,
        session_ingress_token=token,
        api_base_url=base_url,
        sources=tuple(sources_raw) if isinstance(sources_raw, list) else (),
        auth=tuple(auth_raw) if isinstance(auth_raw, list) else (),
        claude_code_args=parsed.get('claude_code_args'),
        mcp_config=parsed.get('mcp_config'),
        environment_variables=parsed.get('environment_variables'),
        use_code_sessions=parsed.get('use_code_sessions'),
        raw=parsed,
    )


def build_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a session-ingress WS URL from an HTTP base + session ID.

    Mirrors ``workSecret.ts:41-48``: localhost gets ``ws://`` + ``/v2/``
    (direct to session-ingress, no Envoy rewrite); production gets
    ``wss://`` + ``/v1/`` (Envoy rewrites ``/v1/`` → ``/v2/``).
    """
    parsed = urlparse(api_base_url)
    hostname = parsed.hostname or ''
    is_localhost = hostname in ('localhost', '127.0.0.1')
    protocol = 'ws' if is_localhost else 'wss'
    version = 'v2' if is_localhost else 'v1'
    # Strip protocol + trailing slashes from the original host part.
    host = api_base_url
    for prefix in ('https://', 'http://'):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    host = host.rstrip('/')
    return f'{protocol}://{host}/{version}/session_ingress/ws/{session_id}'


def build_ccr_v2_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a CCR v2 session URL (HTTP base for /v1/code/sessions/{id}).

    Mirrors ``workSecret.ts:81-87``. The child CC derives the SSE stream
    path and worker endpoints from this URL.
    """
    base = api_base_url.rstrip('/')
    return f'{base}/v1/code/sessions/{session_id}'


def same_session_id(a: str, b: str) -> bool:
    """Compare two session IDs regardless of the tagged-ID prefix family.

    Tagged IDs have the shape ``{tag}_{body}`` or ``{tag}_staging_{body}``,
    where the body encodes a UUID. CCR v2's compat layer returns
    ``session_*`` to v1 API clients but the infra layer uses ``cse_*``;
    both have the same underlying UUID. Without this, ``replBridge``
    rejects its own session as "foreign" at the work-received check when
    the ``ccr_v2_compat_enabled`` gate is on.

    Mirrors ``workSecret.ts:62-73``.
    """
    if a == b:
        return True
    a_body = a[a.rfind('_') + 1:]
    b_body = b[b.rfind('_') + 1:]
    # Guard against IDs with no underscore (bare UUIDs): rfind returns -1,
    # slice starts at 0, returns the whole string. We already handled
    # ``a == b`` above; require min length 4 to avoid false matches on
    # short suffixes (e.g., single-char tag remnants).
    return len(a_body) >= 4 and a_body == b_body


__all__ = [
    'WorkSecret',
    'build_ccr_v2_sdk_url',
    'build_sdk_url',
    'decode_work_secret',
    'same_session_id',
]
