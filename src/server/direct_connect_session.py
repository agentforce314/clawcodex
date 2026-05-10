"""Direct Connect session creation (client side of POST /sessions).

Ports ``typescript/src/server/createDirectConnectSession.ts:11-88``.

Posts to ``${server_url}/sessions``, validates the response via
``validate_connect_response``, returns a ``DirectConnectConfig`` ready
for use by ``DirectConnectSessionManager``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .types import validate_connect_response


class DirectConnectError(Exception):
    """Network, HTTP, or response-parsing failure during connect."""


@dataclass(frozen=True)
class DirectConnectConfig:
    """Immutable config returned by ``create_direct_connect_session``.

    Mirrors TS ``DirectConnectConfig`` at
    ``server/directConnectManager.ts:13-18``. The ``auth_token`` is the
    same token the server granted at session create; the WS upgrade
    sends it as ``Authorization: Bearer ...``.
    """

    server_url: str
    session_id: str
    ws_url: str
    auth_token: str | None = None


async def create_direct_connect_session(
    *,
    server_url: str,
    cwd: str,
    auth_token: str | None = None,
    dangerously_skip_permissions: bool = False,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[DirectConnectConfig, str | None]:
    """POST ``/sessions`` to ``server_url`` and return ``(config, work_dir)``.

    Mirrors ``createDirectConnectSession.ts:26-88``.

    Raises ``DirectConnectError`` on:
      - network failure (DNS, connection refused, timeout)
      - non-2xx HTTP response
      - invalid response payload (missing ``session_id`` or ``ws_url``)

    The ``client`` parameter is for tests; production callers omit it
    and a fresh ``AsyncClient`` is constructed per call.
    """
    headers: dict[str, str] = {'content-type': 'application/json'}
    if auth_token:
        headers['authorization'] = f'Bearer {auth_token}'

    body: dict[str, Any] = {'cwd': cwd}
    if dangerously_skip_permissions:
        body['dangerously_skip_permissions'] = True

    url = f'{server_url.rstrip("/")}/sessions'

    async def _do_post(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(url, json=body, headers=headers, timeout=timeout_seconds)

    try:
        if client is None:
            async with httpx.AsyncClient() as fresh:
                resp = await _do_post(fresh)
        else:
            resp = await _do_post(client)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        raise DirectConnectError(
            f'Failed to connect to server at {server_url}: {exc}'
        ) from exc

    if resp.status_code < 200 or resp.status_code >= 300:
        raise DirectConnectError(
            f'Failed to create session: {resp.status_code} {resp.reason_phrase}'
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise DirectConnectError(f'Invalid session response: not valid JSON: {exc}') from exc

    try:
        validated = validate_connect_response(payload)
    except ValueError as exc:
        raise DirectConnectError(f'Invalid session response: {exc}') from exc

    # Prefer the server-issued per-session token over the caller-supplied
    # bootstrap token: the server's POST /sessions response includes a
    # short-lived token in ``auth_token`` for the WS upgrade. Falling back
    # to the bootstrap token preserves compatibility with servers that
    # don't issue per-session tokens.
    issued_token = payload.get('auth_token') if isinstance(payload, dict) else None
    effective_token = issued_token if isinstance(issued_token, str) and issued_token else auth_token
    config = DirectConnectConfig(
        server_url=server_url,
        session_id=validated['session_id'],
        ws_url=validated['ws_url'],
        auth_token=effective_token,
    )
    work_dir = validated.get('work_dir')
    return config, work_dir


__all__ = [
    'DirectConnectConfig',
    'DirectConnectError',
    'create_direct_connect_session',
]
