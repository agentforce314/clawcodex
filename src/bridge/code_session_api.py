"""HTTP wrappers for the CCR v2 code-session API + worker registration.

Ports ``typescript/src/bridge/codeSessionApi.ts:26-168`` and the
``register_worker`` half of ``workSecret.ts:97-127``.

Three calls:

  * ``create_code_session(base_url, access_token, title, ...)`` — POST
    ``/v1/code/sessions`` and return the new session ID (``cse_*``).
  * ``fetch_remote_credentials(session_id, base_url, access_token, ...)``
    — POST ``/v1/code/sessions/{id}/bridge`` and return the
    ``RemoteCredentials`` (``worker_jwt``/``api_base_url``/
    ``expires_in``/``worker_epoch``). Each call to ``/bridge`` IS the
    registration; the server bumps ``worker_epoch`` on every call.
  * ``register_worker(session_url, access_token)`` — POST
    ``/worker/register`` (legacy v2 path used when ``/bridge`` did not
    return an epoch). Returns the ``worker_epoch`` integer.

All return ``None`` on best-effort failure (network, non-2xx, malformed
response) — matches TS ``return null`` rather than raising. Callers log
and continue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .protojson import coerce_int64

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = '2023-06-01'

DEFAULT_TIMEOUT_SECONDS = 30.0


def _oauth_headers(access_token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'anthropic-version': ANTHROPIC_VERSION,
    }


@dataclass(frozen=True)
class RemoteCredentials:
    """Output of ``fetch_remote_credentials`` (POST /bridge).

    Mirrors TS ``RemoteCredentials`` at ``codeSessionApi.ts:84-91``.
    The JWT is opaque — callers should not decode it (use
    ``schedule_from_expires_in`` for the refresh timer, not
    ``decode_jwt_expiry``).
    """

    worker_jwt: str
    api_base_url: str
    expires_in: int
    worker_epoch: int


# ─── POST /v1/code/sessions ────────────────────────────────────────────────


async def create_code_session(
    base_url: str,
    access_token: str,
    title: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    tags: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """POST ``/v1/code/sessions`` and return the new session ID.

    Body: ``{title, bridge: {}, tags?}`` — the ``bridge: {}`` is the
    positive signal for the oneof runner; omitting it 400s.

    Returns ``None`` on any failure (network, non-2xx, missing or
    malformed ``session.id``); the caller logs and falls through.

    Mirrors ``codeSessionApi.ts:26-80``.
    """
    url = f'{base_url.rstrip("/")}/v1/code/sessions'
    body: dict[str, Any] = {'title': title, 'bridge': {}}
    if tags:
        body['tags'] = tags

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_seconds) as fresh:
                resp = await fresh.post(url, json=body, headers=_oauth_headers(access_token))
        else:
            resp = await client.post(
                url, json=body, headers=_oauth_headers(access_token), timeout=timeout_seconds,
            )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug('[code-session] Session create request failed: %s', exc)
        return None

    if resp.status_code not in (200, 201):
        logger.debug(
            '[code-session] Session create failed %d', resp.status_code
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    session_obj = data.get('session') if isinstance(data, dict) else None
    if not isinstance(session_obj, dict):
        return None
    sid = session_obj.get('id')
    if not isinstance(sid, str) or not sid.startswith('cse_'):
        logger.debug('[code-session] No session.id (cse_*) in response')
        return None
    return sid


# ─── POST /v1/code/sessions/{id}/bridge ────────────────────────────────────


async def fetch_remote_credentials(
    session_id: str,
    base_url: str,
    access_token: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    trusted_device_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> RemoteCredentials | None:
    """POST ``/v1/code/sessions/{id}/bridge`` and return credentials.

    Mirrors ``codeSessionApi.ts:93-168``. ``trusted_device_token`` is
    passed as ``X-Trusted-Device-Token`` if set; otherwise omitted.

    Returns ``None`` on failure. ``worker_epoch`` is coerced via
    ``coerce_int64`` (handles protojson string-OR-number).
    """
    url = f'{base_url.rstrip("/")}/v1/code/sessions/{session_id}/bridge'
    headers = _oauth_headers(access_token)
    if trusted_device_token:
        headers['X-Trusted-Device-Token'] = trusted_device_token

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_seconds) as fresh:
                resp = await fresh.post(url, json={}, headers=headers)
        else:
            resp = await client.post(
                url, json={}, headers=headers, timeout=timeout_seconds,
            )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug('[code-session] /bridge request failed: %s', exc)
        return None

    if resp.status_code != 200:
        logger.debug('[code-session] /bridge failed %d', resp.status_code)
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None
    worker_jwt = data.get('worker_jwt')
    api_base = data.get('api_base_url')
    expires_in = data.get('expires_in')
    raw_epoch = data.get('worker_epoch')

    if not isinstance(worker_jwt, str) or not worker_jwt:
        logger.debug('[code-session] /bridge missing worker_jwt')
        return None
    if not isinstance(api_base, str) or not api_base:
        logger.debug('[code-session] /bridge missing api_base_url')
        return None
    if not isinstance(expires_in, int):
        logger.debug('[code-session] /bridge missing or invalid expires_in')
        return None
    if raw_epoch is None:
        logger.debug('[code-session] /bridge missing worker_epoch')
        return None
    try:
        epoch = coerce_int64(raw_epoch)
    except ValueError as exc:
        logger.debug('[code-session] /bridge worker_epoch invalid: %s', exc)
        return None
    return RemoteCredentials(
        worker_jwt=worker_jwt,
        api_base_url=api_base,
        expires_in=expires_in,
        worker_epoch=epoch,
    )


# ─── POST /worker/register (legacy path) ───────────────────────────────────


async def register_worker(
    session_url: str,
    access_token: str,
    *,
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> int:
    """POST ``${session_url}/worker/register`` and return the ``worker_epoch``.

    Used by the v1 CCR-v2 path (``replBridge`` poll loop) when ``/bridge``
    didn't return an epoch directly. Raises ``RuntimeError`` on
    network/HTTP failure or malformed response — callers wrap in their
    own retry budget.

    Mirrors ``workSecret.ts:97-127``.
    """
    url = f'{session_url.rstrip("/")}/worker/register'
    headers = _oauth_headers(access_token)
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_seconds) as fresh:
                resp = await fresh.post(url, json={}, headers=headers)
        else:
            resp = await client.post(
                url, json={}, headers=headers, timeout=timeout_seconds,
            )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        raise RuntimeError(f'register_worker failed: {exc}') from exc

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f'register_worker: unexpected status {resp.status_code}'
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f'register_worker: invalid JSON response: {exc}') from exc

    if not isinstance(data, dict):
        raise RuntimeError('register_worker: response is not an object')
    raw = data.get('worker_epoch')
    if raw is None:
        raise RuntimeError('register_worker: response missing worker_epoch')
    try:
        return coerce_int64(raw)
    except ValueError as exc:
        raise RuntimeError(
            f'register_worker: invalid worker_epoch: {exc}'
        ) from exc


__all__ = [
    'ANTHROPIC_VERSION',
    'DEFAULT_TIMEOUT_SECONDS',
    'RemoteCredentials',
    'create_code_session',
    'fetch_remote_credentials',
    'register_worker',
]
