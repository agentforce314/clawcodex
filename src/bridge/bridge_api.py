"""OAuth-authenticated HTTP client for the bridge environments API.

Ports ``typescript/src/bridge/bridgeApi.ts``.

Wraps the ``/v1/environments/bridge/*``, ``/v1/sessions/*``, and ``/v1/work/*``
endpoints behind the ``BridgeApiClient`` Protocol (defined in
``src.bridge.types``). The client handles:

* OAuth bearer auth + ``X-Trusted-Device-Token`` + ``anthropic-version`` +
  ``anthropic-beta: environments-2025-11-01`` headers.
* Path-segment validation (``validate_bridge_id``) so server-provided IDs
  can't trigger path traversal.
* One-shot 401 retry via the injected ``on_auth_401`` callback (mirrors
  ``handleOAuth401Error`` in TS) — only on mutation endpoints that use
  OAuth tokens directly; poll / ack / heartbeat use the environment
  secret or session token so they don't go through the retry wrapper.
* ``BridgeFatalError`` for 401/403/404/410 plus standard ``Exception``
  for 429 / other 5xx-suppressed errors. ``handle_error_status`` is the
  central router.
* Empty-poll log throttling (first poll + every 100th) to keep debug
  logs readable when the bridge sits idle for hours.

Backed by ``httpx.AsyncClient``. Tests inject a ``MockTransport`` via
the ``client`` parameter to ``create_bridge_api_client`` (same pattern
as ``code_session_api.py``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

import httpx

from src.bridge.debug_utils import debug_body, extract_error_detail
from src.bridge.exceptions import BridgeFatalError
from src.bridge.types import (
    BRIDGE_LOGIN_INSTRUCTION,
    BridgeApiClient,
    BridgeConfig,
    PermissionResponseEvent,
    WorkResponse,
)

logger = logging.getLogger(__name__)


# Anthropic-beta header value for the environments API. Mirrors TS
# ``BETA_HEADER`` on ``bridgeApi.ts:38``.
BETA_HEADER = 'environments-2025-11-01'

ANTHROPIC_VERSION = '2023-06-01'

# httpx default timeout for bridge requests (seconds). Matches TS
# axios ``timeout: 10_000`` / ``15_000`` on the registration call.
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
_REGISTRATION_TIMEOUT_SECONDS = 15.0


# Allowlist pattern for server-provided IDs used in URL path segments.
# Mirrors TS ``SAFE_ID_PATTERN`` on ``bridgeApi.ts:41``.
_SAFE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def validate_bridge_id(value: str, label: str) -> str:
    """Validate that a server-provided ID is safe to interpolate into URLs.

    Mirrors TS ``validateBridgeId`` on ``bridgeApi.ts:48-53``. Rejects
    path traversal (``../../admin``) and IDs containing slashes, dots, or
    other special characters. Raises ``ValueError`` on rejection so the
    bug surfaces at the call site rather than producing a malformed URL.
    """
    if not value or not _SAFE_ID_PATTERN.match(value):
        raise ValueError(f'Invalid {label}: contains unsafe characters')
    return value


# ── Public predicates ─────────────────────────────────────────────────────


def is_expired_error_type(error_type: str | None) -> bool:
    """Mirrors TS ``isExpiredErrorType`` on ``bridgeApi.ts:503-508``.

    Used by callers to distinguish "session expired, recreate" from
    "permission denied, fail loud" on a 403/410.
    """
    if not error_type:
        return False
    return 'expired' in error_type or 'lifetime' in error_type


def is_suppressible_403(err: BridgeFatalError) -> bool:
    """Whether a 403 is a known-suppressible scope/permission error.

    Mirrors TS ``isSuppressible403`` on ``bridgeApi.ts:516-524``. Some
    403s are for scopes like ``external_poll_sessions`` or
    ``environments:manage`` that don't affect core functionality —
    callers can hide these from the user.
    """
    if err.status != 403:
        return False
    message = str(err)
    return (
        'external_poll_sessions' in message
        or 'environments:manage' in message
    )


# ── Factory + client implementation ───────────────────────────────────────


OnDebug = Callable[[str], None]
OnAuth401 = Callable[[str], Awaitable[bool]]
GetAccessToken = Callable[[], str | None]
GetTrustedDeviceToken = Callable[[], str | None]


def create_bridge_api_client(
    *,
    base_url: str,
    get_access_token: GetAccessToken,
    runner_version: str,
    on_debug: OnDebug | None = None,
    on_auth_401: OnAuth401 | None = None,
    get_trusted_device_token: GetTrustedDeviceToken | None = None,
    client: httpx.AsyncClient | None = None,
) -> BridgeApiClient:
    """Factory mirroring TS ``createBridgeApiClient(deps)``.

    Returns an object that satisfies the ``BridgeApiClient`` Protocol
    from ``src.bridge.types``.

    Args (kw-only to match the readability of the TS options object):
        base_url: Bridge API root URL (e.g. ``https://api.anthropic.com``).
        get_access_token: Sync callable returning the current OAuth token
            or ``None``. Called before every OAuth-authed request.
        runner_version: Version string sent as the
            ``x-environment-runner-version`` header.
        on_debug: Optional sync callable for debug log lines. When absent,
            messages route to the module logger at DEBUG level.
        on_auth_401: Optional async callback invoked on a 401. Should
            attempt to refresh the OAuth token and return ``True`` on
            success. When absent, 401s go straight to ``BridgeFatalError``.
            See TS comment on ``bridgeApi.ts:17-25`` for why this is
            injected rather than imported.
        get_trusted_device_token: Optional sync callable returning the
            ``X-Trusted-Device-Token`` header value, or ``None`` to omit
            the header.
        client: Optional ``httpx.AsyncClient`` for test injection. When
            ``None``, a fresh client is created per request (and closed
            after) — fine for tests/scripts but loses connection pooling
            and adds ~10ms TLS handshake per call. **Strongly recommended
            to pass a long-lived client in production** (especially for
            the polling loop in Phase 6 — at 2s poll interval that's
            ~300 wasted handshakes/hour).
    """
    return _BridgeApiClient(
        base_url=base_url,
        get_access_token=get_access_token,
        runner_version=runner_version,
        on_debug=on_debug,
        on_auth_401=on_auth_401,
        get_trusted_device_token=get_trusted_device_token,
        client=client,
    )


class _BridgeApiClient:
    """Concrete implementation of ``BridgeApiClient``.

    Not exported — callers construct via ``create_bridge_api_client`` so
    the Protocol stays the public surface.
    """

    _EMPTY_POLL_LOG_INTERVAL = 100

    def __init__(
        self,
        *,
        base_url: str,
        get_access_token: GetAccessToken,
        runner_version: str,
        on_debug: OnDebug | None,
        on_auth_401: OnAuth401 | None,
        get_trusted_device_token: GetTrustedDeviceToken | None,
        client: httpx.AsyncClient | None,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._get_access_token = get_access_token
        self._runner_version = runner_version
        self._on_debug = on_debug
        self._on_auth_401 = on_auth_401
        self._get_trusted_device_token = get_trusted_device_token
        self._client = client
        self._consecutive_empty_polls = 0

    # ── internal helpers ────────────────────────────────────────────────

    def _debug(self, msg: str) -> None:
        if self._on_debug is not None:
            self._on_debug(msg)
        else:
            logger.debug(msg)

    def _headers(self, access_token: str) -> dict[str, str]:
        """Mirror TS ``getHeaders`` on ``bridgeApi.ts:76-89``."""
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'anthropic-version': ANTHROPIC_VERSION,
            'anthropic-beta': BETA_HEADER,
            'x-environment-runner-version': self._runner_version,
        }
        if self._get_trusted_device_token is not None:
            token = self._get_trusted_device_token()
            if token:
                headers['X-Trusted-Device-Token'] = token
        return headers

    def _resolve_auth(self) -> str:
        """Mirror TS ``resolveAuth`` on ``bridgeApi.ts:91-97``.

        **Behavioral divergence (intentional)**: TS throws a plain
        ``Error(BRIDGE_LOGIN_INSTRUCTION)``; we throw a
        ``BridgeFatalError(status=401)`` so callers can catch the typed
        error and inspect ``.status`` rather than string-matching the
        message. Same observable failure (request never goes out), but
        Python callers get a richer signal.
        """
        token = self._get_access_token()
        if not token:
            raise BridgeFatalError(BRIDGE_LOGIN_INSTRUCTION, status=401)
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> httpx.Response:
        """Single HTTP request via the injected or freshly-created client.

        On the no-injected-client fallback path, a fresh ``AsyncClient``
        is constructed per call — TLS handshake + no connection pooling —
        which is fine for tests / scripts but suboptimal for polling
        loops. Production callers should pass a long-lived ``client``;
        see the factory docstring.
        """
        url = f'{self._base_url}{path}'
        kwargs: dict[str, Any] = {
            'headers': self._headers(access_token),
            'timeout': timeout_seconds,
        }
        if json_body is not None:
            kwargs['json'] = json_body
        if params is not None:
            kwargs['params'] = params
        if self._client is not None:
            return await self._client.request(method, url, **kwargs)
        return await self._send_with_fresh_client(method, url, kwargs)

    async def _send_with_fresh_client(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any],
    ) -> httpx.Response:
        """Per-request client fallback. Extracted as a seam for tests.

        Tests monkeypatch this method to verify the no-injected-client
        production path (factory called without ``client=``). Production
        callers should not subclass.
        """
        async with httpx.AsyncClient() as client:
            return await client.request(method, url, **kwargs)

    async def _with_oauth_retry(
        self,
        do_request: Callable[[str], Awaitable[httpx.Response]],
        context: str,
    ) -> httpx.Response:
        """Execute an OAuth-authed request, retrying once on 401.

        Mirrors TS ``withOAuthRetry`` on ``bridgeApi.ts:106-139``. The
        retry only fires when ``on_auth_401`` is wired and returns
        ``True``; otherwise the 401 falls through to
        ``_handle_error_status`` which raises ``BridgeFatalError``.
        """
        access_token = self._resolve_auth()
        response = await do_request(access_token)
        if response.status_code != 401:
            return response
        if self._on_auth_401 is None:
            self._debug(
                f'[bridge:api] {context}: 401 received, no refresh handler'
            )
            return response
        self._debug(
            f'[bridge:api] {context}: 401 received, attempting token refresh'
        )
        refreshed = await self._on_auth_401(access_token)
        if refreshed:
            self._debug(
                f'[bridge:api] {context}: Token refreshed, retrying request'
            )
            new_token = self._resolve_auth()
            retry_response = await do_request(new_token)
            if retry_response.status_code != 401:
                return retry_response
            self._debug(
                f'[bridge:api] {context}: Retry after refresh also got 401'
            )
        else:
            self._debug(f'[bridge:api] {context}: Token refresh failed')
        return response

    # ── BridgeApiClient methods ─────────────────────────────────────────

    async def register_bridge_environment(
        self, config: BridgeConfig
    ) -> dict[str, str]:
        """POST ``/v1/environments/bridge``. Mirror TS lines 142-197."""
        self._debug(
            f'[bridge:api] POST /v1/environments/bridge '
            f'bridgeId={config.bridge_id}'
        )

        body: dict[str, Any] = {
            'machine_name': config.machine_name,
            'directory': config.dir,
            'branch': config.branch,
            'git_repo_url': config.git_repo_url,
            'max_sessions': config.max_sessions,
            'metadata': {'worker_type': config.worker_type},
        }
        if config.reuse_environment_id:
            body['environment_id'] = config.reuse_environment_id

        async def do(access_token: str) -> httpx.Response:
            return await self._request(
                'POST',
                '/v1/environments/bridge',
                access_token=access_token,
                json_body=body,
                timeout_seconds=_REGISTRATION_TIMEOUT_SECONDS,
            )

        response = await self._with_oauth_retry(do, 'Registration')
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'Registration')
        # Mirror the defensive guard pattern from heartbeat_work — a 200
        # with a non-JSON body (gateway HTML page, truncated response,
        # content-type mismatch) surfaces as ``data == None`` here, and
        # ``data.get(...)`` below would crash with ``AttributeError``.
        if not isinstance(data, dict):
            raise BridgeFatalError(
                'Registration: malformed response — non-JSON body',
                status=response.status_code,
            )
        env_id = data.get('environment_id')
        env_secret = data.get('environment_secret')
        if not isinstance(env_id, str) or not isinstance(env_secret, str):
            raise BridgeFatalError(
                'Registration: malformed response — '
                'missing environment_id / environment_secret',
                status=response.status_code,
            )
        self._debug(
            f'[bridge:api] POST /v1/environments/bridge -> '
            f'{response.status_code} environment_id={env_id}'
        )
        self._debug(f'[bridge:api] >>> {debug_body(body)}')
        self._debug(f'[bridge:api] <<< {debug_body(data)}')
        return {'environment_id': env_id, 'environment_secret': env_secret}

    async def poll_for_work(
        self,
        environment_id: str,
        environment_secret: str,
        cancel_event: Any | None = None,  # noqa: ARG002  Phase 5 wiring
        reclaim_older_than_ms: int | None = None,
    ) -> WorkResponse | None:
        """GET ``.../work/poll``. Mirror TS lines 199-247.

        ``cancel_event`` is accepted for Protocol parity but not yet
        wired — Phase 5 will inject an ``asyncio.Event`` and we'll
        connect it via ``create_combined_abort_signal``. For now httpx's
        request-level timeout handles slow polls; caller-driven
        cancellation requires only task cancellation (``task.cancel()``)
        which httpx honors natively.
        """
        validate_bridge_id(environment_id, 'environment_id')

        # Reset and capture; restore only when the response is truly empty.
        prev_empty_polls = self._consecutive_empty_polls
        self._consecutive_empty_polls = 0

        params: dict[str, Any] | None = (
            {'reclaim_older_than_ms': reclaim_older_than_ms}
            if reclaim_older_than_ms is not None
            else None
        )
        response = await self._request(
            'GET',
            f'/v1/environments/{environment_id}/work/poll',
            access_token=environment_secret,
            params=params,
        )
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'Poll')

        # ``data is None`` → no work. Matches TS ``!response.data`` for the
        # null branch exactly. Anything else (including malformed empty
        # dict ``{}``) falls through so the orchestrator can detect a
        # server-contract violation rather than silently treating it as
        # "no work."
        if data is None:
            self._consecutive_empty_polls = prev_empty_polls + 1
            if (
                self._consecutive_empty_polls == 1
                or self._consecutive_empty_polls % self._EMPTY_POLL_LOG_INTERVAL == 0
            ):
                self._debug(
                    f'[bridge:api] GET .../work/poll -> '
                    f'{response.status_code} (no work, '
                    f'{self._consecutive_empty_polls} consecutive empty polls)'
                )
            return None

        inner = data.get('data') if isinstance(data, dict) else None
        inner_type = inner.get('type') if isinstance(inner, dict) else None
        inner_id = inner.get('id') if isinstance(inner, dict) else None
        self._debug(
            f'[bridge:api] GET .../work/poll -> {response.status_code} '
            f'workId={data.get("id")} type={inner_type}'
            + (f' sessionId={inner_id}' if inner_id else '')
        )
        self._debug(f'[bridge:api] <<< {debug_body(data)}')
        return data  # type: ignore[return-value]

    async def acknowledge_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> None:
        """POST ``.../work/{id}/ack``. Mirror TS lines 249-271."""
        validate_bridge_id(environment_id, 'environment_id')
        validate_bridge_id(work_id, 'work_id')
        self._debug(f'[bridge:api] POST .../work/{work_id}/ack')
        response = await self._request(
            'POST',
            f'/v1/environments/{environment_id}/work/{work_id}/ack',
            access_token=session_token,
            json_body={},
        )
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'Acknowledge')
        self._debug(
            f'[bridge:api] POST .../work/{work_id}/ack -> {response.status_code}'
        )

    async def stop_work(
        self, environment_id: str, work_id: str, force: bool
    ) -> None:
        """POST ``.../work/{id}/stop``. Mirror TS lines 273-299."""
        validate_bridge_id(environment_id, 'environment_id')
        validate_bridge_id(work_id, 'work_id')
        self._debug(
            f'[bridge:api] POST .../work/{work_id}/stop force={force}'
        )

        async def do(access_token: str) -> httpx.Response:
            return await self._request(
                'POST',
                f'/v1/environments/{environment_id}/work/{work_id}/stop',
                access_token=access_token,
                json_body={'force': force},
            )

        response = await self._with_oauth_retry(do, 'StopWork')
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'StopWork')
        self._debug(
            f'[bridge:api] POST .../work/{work_id}/stop -> {response.status_code}'
        )

    async def deregister_environment(self, environment_id: str) -> None:
        """DELETE ``/v1/environments/bridge/{id}``. Mirror TS lines 301-323."""
        validate_bridge_id(environment_id, 'environment_id')
        self._debug(
            f'[bridge:api] DELETE /v1/environments/bridge/{environment_id}'
        )

        async def do(access_token: str) -> httpx.Response:
            return await self._request(
                'DELETE',
                f'/v1/environments/bridge/{environment_id}',
                access_token=access_token,
            )

        response = await self._with_oauth_retry(do, 'Deregister')
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'Deregister')
        self._debug(
            f'[bridge:api] DELETE /v1/environments/bridge/{environment_id} '
            f'-> {response.status_code}'
        )

    async def archive_session(self, session_id: str) -> None:
        """POST ``/v1/sessions/{id}/archive``. Mirror TS lines 325-356.

        409 is treated as success (idempotent — session already archived).
        """
        validate_bridge_id(session_id, 'session_id')
        self._debug(f'[bridge:api] POST /v1/sessions/{session_id}/archive')

        async def do(access_token: str) -> httpx.Response:
            return await self._request(
                'POST',
                f'/v1/sessions/{session_id}/archive',
                access_token=access_token,
                json_body={},
            )

        response = await self._with_oauth_retry(do, 'ArchiveSession')
        if response.status_code == 409:
            self._debug(
                f'[bridge:api] POST /v1/sessions/{session_id}/archive '
                f'-> 409 (already archived)'
            )
            return
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'ArchiveSession')
        self._debug(
            f'[bridge:api] POST /v1/sessions/{session_id}/archive '
            f'-> {response.status_code}'
        )

    async def reconnect_session(
        self, environment_id: str, session_id: str
    ) -> None:
        """POST ``.../bridge/reconnect``. Mirror TS lines 358-385."""
        validate_bridge_id(environment_id, 'environment_id')
        validate_bridge_id(session_id, 'session_id')
        self._debug(
            f'[bridge:api] POST /v1/environments/{environment_id}'
            f'/bridge/reconnect session_id={session_id}'
        )

        async def do(access_token: str) -> httpx.Response:
            return await self._request(
                'POST',
                f'/v1/environments/{environment_id}/bridge/reconnect',
                access_token=access_token,
                json_body={'session_id': session_id},
            )

        response = await self._with_oauth_retry(do, 'ReconnectSession')
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'ReconnectSession')
        self._debug(
            f'[bridge:api] POST .../bridge/reconnect -> {response.status_code}'
        )

    async def heartbeat_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> dict[str, Any]:
        """POST ``.../work/{id}/heartbeat``. Mirror TS lines 387-417."""
        validate_bridge_id(environment_id, 'environment_id')
        validate_bridge_id(work_id, 'work_id')
        self._debug(f'[bridge:api] POST .../work/{work_id}/heartbeat')
        response = await self._request(
            'POST',
            f'/v1/environments/{environment_id}/work/{work_id}/heartbeat',
            access_token=session_token,
            json_body={},
        )
        data = _safe_json(response)
        _handle_error_status(response.status_code, data, 'Heartbeat')
        if not isinstance(data, dict):
            raise BridgeFatalError(
                'Heartbeat: malformed response',
                status=response.status_code,
            )
        self._debug(
            f'[bridge:api] POST .../work/{work_id}/heartbeat -> '
            f'{response.status_code} lease_extended={data.get("lease_extended")} '
            f'state={data.get("state")}'
        )
        return data

    async def send_permission_response_event(
        self,
        session_id: str,
        event: PermissionResponseEvent,
        session_token: str,
    ) -> None:
        """POST ``/v1/sessions/{id}/events``. Mirror TS lines 419-450."""
        validate_bridge_id(session_id, 'session_id')
        event_type = event.get('type', '<unknown>')
        self._debug(
            f'[bridge:api] POST /v1/sessions/{session_id}/events '
            f'type={event_type}'
        )
        body = {'events': [event]}
        response = await self._request(
            'POST',
            f'/v1/sessions/{session_id}/events',
            access_token=session_token,
            json_body=body,
        )
        data = _safe_json(response)
        _handle_error_status(
            response.status_code, data, 'SendPermissionResponseEvent'
        )
        self._debug(
            f'[bridge:api] POST /v1/sessions/{session_id}/events '
            f'-> {response.status_code}'
        )
        self._debug(f'[bridge:api] >>> {debug_body(body)}')
        self._debug(f'[bridge:api] <<< {debug_body(data)}')


# ── Error handling ────────────────────────────────────────────────────────


def _extract_error_type_from_data(data: Any) -> str | None:
    """Pull ``data.error.type`` from a structured error body.

    Mirrors TS ``extractErrorTypeFromData`` on ``bridgeApi.ts:526-539``.
    Internal helper — not exported (but tested via the public error
    paths it feeds into).
    """
    if not isinstance(data, dict):
        return None
    error = data.get('error')
    if not isinstance(error, dict):
        return None
    error_type = error.get('type')
    if isinstance(error_type, str):
        return error_type
    return None


def _safe_json(response: httpx.Response) -> Any:
    """Best-effort ``response.json()`` — returns ``None`` on parse error."""
    try:
        return response.json()
    except (ValueError, httpx.DecodingError):
        return None


def _handle_error_status(
    status: int,
    data: Any,
    context: str,
) -> None:
    """Raise ``BridgeFatalError`` (or generic ``Exception``) on non-2xx.

    Mirrors TS ``handleErrorStatus`` on ``bridgeApi.ts:454-500``. Maps
    each well-known status to a typed exception with the most useful
    diagnostic message we can build from the response body.

    200 / 204 → no-op.
    401 → BridgeFatalError(401) with login instruction.
    403 → BridgeFatalError(403); special-cases expired-error-type as
          "Remote Control session has expired".
    404 → BridgeFatalError(404); detail-or-default message.
    410 → BridgeFatalError(410) tagged ``environment_expired``.
    429 → Plain ``Exception`` (not fatal; caller backs off).
    other → Plain ``Exception``.
    """
    if status in (200, 204):
        return
    detail = extract_error_detail(data)
    error_type = _extract_error_type_from_data(data)

    if status == 401:
        raise BridgeFatalError(
            f'{context}: Authentication failed (401)'
            + (f': {detail}' if detail else '')
            + f'. {BRIDGE_LOGIN_INSTRUCTION}',
            status=401,
            error_type=error_type,
        )
    if status == 403:
        if is_expired_error_type(error_type):
            message = (
                'Remote Control session has expired. Please restart '
                'with `claude remote-control` or /remote-control.'
            )
        else:
            message = (
                f'{context}: Access denied (403)'
                + (f': {detail}' if detail else '')
                + '. Check your organization permissions.'
            )
        raise BridgeFatalError(message, status=403, error_type=error_type)
    if status == 404:
        raise BridgeFatalError(
            detail
            or (
                f'{context}: Not found (404). Remote Control may not be '
                'available for this organization.'
            ),
            status=404,
            error_type=error_type,
        )
    if status == 410:
        raise BridgeFatalError(
            detail
            or (
                'Remote Control session has expired. Please restart with '
                '`claude remote-control` or /remote-control.'
            ),
            status=410,
            error_type=error_type or 'environment_expired',
        )
    if status == 429:
        raise Exception(
            f'{context}: Rate limited (429). Polling too frequently.'
        )
    raise Exception(
        f'{context}: Failed with status {status}'
        + (f': {detail}' if detail else '')
    )


__all__ = [
    'ANTHROPIC_VERSION',
    'BETA_HEADER',
    'BridgeFatalError',  # re-exported for callers that catch it
    'create_bridge_api_client',
    'is_expired_error_type',
    'is_suppressible_403',
    'validate_bridge_id',
]
