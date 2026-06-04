"""Tests for ``src.bridge.bridge_api`` — the bridge HTTP client.

Uses ``httpx.MockTransport`` (same pattern as ``test_code_session_api``)
to intercept requests and return scripted responses. No real network.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from src.bridge import bridge_api
from src.bridge.bridge_api import (
    ANTHROPIC_VERSION,
    BETA_HEADER,
    create_bridge_api_client,
    is_expired_error_type,
    is_suppressible_403,
    validate_bridge_id,
)
from src.bridge.exceptions import BridgeFatalError
from src.bridge.types import BridgeConfig


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_config(**overrides: Any) -> BridgeConfig:
    defaults = dict(
        dir='/tmp/repo',
        machine_name='test-host',
        branch='main',
        git_repo_url='https://github.com/owner/repo',
        max_sessions=1,
        spawn_mode='single-session',
        verbose=False,
        sandbox=False,
        bridge_id='br-1',
        worker_type='claude_code',
        environment_id='env-client-1',
        api_base_url='https://api.example.com',
        session_ingress_url='https://api.example.com',
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = 'https://api.example.com',
    get_access_token: Callable[[], str | None] | None = None,
    on_auth_401: Any = None,
    get_trusted_device_token: Callable[[], str | None] | None = None,
    runner_version: str = 'py-test-0.1',
):
    return create_bridge_api_client(
        base_url=base_url,
        get_access_token=get_access_token or (lambda: 'tok-1'),
        runner_version=runner_version,
        on_auth_401=on_auth_401,
        get_trusted_device_token=get_trusted_device_token,
        client=_mock_client(handler),
    )


# ── Pure helpers ──────────────────────────────────────────────────────────


def test_validate_bridge_id_accepts_alphanumeric_and_dash_underscore() -> None:
    assert validate_bridge_id('env_abc-123', 'env') == 'env_abc-123'


def test_validate_bridge_id_rejects_empty() -> None:
    with pytest.raises(ValueError, match='env'):
        validate_bridge_id('', 'env')


@pytest.mark.parametrize(
    'bad', ['../admin', 'has/slash', 'has.dot', 'has space', 'a%40b']
)
def test_validate_bridge_id_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ValueError, match='env'):
        validate_bridge_id(bad, 'env')


def test_is_expired_error_type_matches_substrings() -> None:
    assert is_expired_error_type('environment_expired') is True
    assert is_expired_error_type('lifetime_exceeded') is True
    assert is_expired_error_type('not_found') is False
    assert is_expired_error_type(None) is False
    assert is_expired_error_type('') is False


def test_is_suppressible_403_only_for_known_messages() -> None:
    err = BridgeFatalError(
        'StopWork: Access denied (403): missing scope external_poll_sessions',
        status=403,
    )
    assert is_suppressible_403(err) is True

    other = BridgeFatalError(
        'StopWork: Access denied (403): organization disabled',
        status=403,
    )
    assert is_suppressible_403(other) is False

    not_403 = BridgeFatalError('boom', status=404)
    assert is_suppressible_403(not_403) is False


# ── Headers + auth ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_includes_oauth_and_beta_headers() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['headers'] = dict(req.headers)
        return httpx.Response(200, json={'environment_id': 'e', 'environment_secret': 's'})

    client = _make_client(handler)
    await client.register_bridge_environment(_make_config())

    assert seen['headers']['authorization'] == 'Bearer tok-1'
    assert seen['headers']['content-type'] == 'application/json'
    assert seen['headers']['anthropic-version'] == ANTHROPIC_VERSION
    assert seen['headers']['anthropic-beta'] == BETA_HEADER
    assert seen['headers']['x-environment-runner-version'] == 'py-test-0.1'
    assert 'x-trusted-device-token' not in seen['headers']


@pytest.mark.asyncio
async def test_request_includes_trusted_device_token_when_provided() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['headers'] = dict(req.headers)
        return httpx.Response(200, json={'environment_id': 'e', 'environment_secret': 's'})

    client = _make_client(handler, get_trusted_device_token=lambda: 'tdt-xyz')
    await client.register_bridge_environment(_make_config())
    assert seen['headers']['x-trusted-device-token'] == 'tdt-xyz'


@pytest.mark.asyncio
async def test_request_omits_trusted_device_token_when_callback_returns_none() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['headers'] = dict(req.headers)
        return httpx.Response(200, json={'environment_id': 'e', 'environment_secret': 's'})

    client = _make_client(handler, get_trusted_device_token=lambda: None)
    await client.register_bridge_environment(_make_config())
    assert 'x-trusted-device-token' not in seen['headers']


@pytest.mark.asyncio
async def test_request_raises_when_no_oauth_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _make_client(handler, get_access_token=lambda: None)
    with pytest.raises(BridgeFatalError):
        await client.register_bridge_environment(_make_config())


# ── register_bridge_environment ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_sends_expected_body() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(req.content)
        seen['url'] = str(req.url)
        return httpx.Response(200, json={
            'environment_id': 'env-srv', 'environment_secret': 'sec-srv'
        })

    client = _make_client(handler)
    cfg = _make_config(branch='feature', max_sessions=3, worker_type='claude_code_assistant')
    out = await client.register_bridge_environment(cfg)

    assert out == {'environment_id': 'env-srv', 'environment_secret': 'sec-srv'}
    assert seen['url'] == 'https://api.example.com/v1/environments/bridge'
    assert seen['body']['machine_name'] == 'test-host'
    assert seen['body']['directory'] == '/tmp/repo'
    assert seen['body']['branch'] == 'feature'
    assert seen['body']['git_repo_url'] == 'https://github.com/owner/repo'
    assert seen['body']['max_sessions'] == 3
    assert seen['body']['metadata'] == {'worker_type': 'claude_code_assistant'}
    assert 'environment_id' not in seen['body']  # no reuse


@pytest.mark.asyncio
async def test_register_includes_environment_id_when_reusing() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(req.content)
        return httpx.Response(200, json={
            'environment_id': 'env-srv', 'environment_secret': 'sec-srv'
        })

    client = _make_client(handler)
    cfg = _make_config(reuse_environment_id='env-prev')
    await client.register_bridge_environment(cfg)
    assert seen['body']['environment_id'] == 'env-prev'


@pytest.mark.asyncio
async def test_register_401_no_handler_raises_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={'error': {'type': 'unauthorized'}})

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError) as exc:
        await client.register_bridge_environment(_make_config())
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_register_410_raises_fatal_with_expired_error_type() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={'message': 'gone'})

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError) as exc:
        await client.register_bridge_environment(_make_config())
    assert exc.value.status == 410
    assert exc.value.error_type == 'environment_expired'


@pytest.mark.asyncio
async def test_register_429_raises_non_fatal_exception() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={'message': 'rate limited'})

    client = _make_client(handler)
    with pytest.raises(Exception) as exc:
        await client.register_bridge_environment(_make_config())
    assert not isinstance(exc.value, BridgeFatalError)
    assert '429' in str(exc.value)


@pytest.mark.asyncio
async def test_register_malformed_response_raises_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'environment_id': 'e'})  # missing secret

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError, match='malformed'):
        await client.register_bridge_environment(_make_config())


# ── OAuth 401 retry ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oauth_retry_refreshes_and_succeeds_on_second_attempt() -> None:
    """Refresh callback returns True → second request must use the refreshed token."""
    tokens = iter(['stale', 'fresh'])
    call_log: list[tuple[str, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get('authorization')
        call_log.append((req.method, auth))
        if auth == 'Bearer stale':
            return httpx.Response(401)
        return httpx.Response(200, json={
            'environment_id': 'e', 'environment_secret': 's'
        })

    refresh_calls: list[str] = []

    async def on_auth_401(stale: str) -> bool:
        refresh_calls.append(stale)
        return True

    client = _make_client(
        handler,
        get_access_token=lambda: next(tokens),
        on_auth_401=on_auth_401,
    )
    out = await client.register_bridge_environment(_make_config())
    assert out['environment_id'] == 'e'
    assert refresh_calls == ['stale']
    assert call_log == [
        ('POST', 'Bearer stale'),
        ('POST', 'Bearer fresh'),
    ]


@pytest.mark.asyncio
async def test_oauth_retry_returns_401_when_refresh_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    async def on_auth_401(_stale: str) -> bool:
        return False

    client = _make_client(handler, on_auth_401=on_auth_401)
    with pytest.raises(BridgeFatalError) as exc:
        await client.register_bridge_environment(_make_config())
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_oauth_retry_returns_401_when_retry_also_401() -> None:
    """Refresh succeeds but retry also gets 401 → fatal."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    refresh_count = [0]

    async def on_auth_401(_stale: str) -> bool:
        refresh_count[0] += 1
        return True

    client = _make_client(handler, on_auth_401=on_auth_401)
    with pytest.raises(BridgeFatalError) as exc:
        await client.register_bridge_environment(_make_config())
    assert exc.value.status == 401
    # Refresh attempted exactly once (no infinite loop).
    assert refresh_count[0] == 1


@pytest.mark.asyncio
async def test_oauth_retry_skipped_for_non_401() -> None:
    """Non-401 errors don't trigger the refresh callback."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    refresh_calls: list[str] = []

    async def on_auth_401(stale: str) -> bool:
        refresh_calls.append(stale)
        return True

    client = _make_client(handler, on_auth_401=on_auth_401)
    with pytest.raises(BridgeFatalError) as exc:
        await client.register_bridge_environment(_make_config())
    assert exc.value.status == 404
    assert refresh_calls == []


# ── poll_for_work ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_returns_none_on_empty_body() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=None)

    client = _make_client(handler)
    out = await client.poll_for_work('env-1', 'env-sec')
    assert out is None


@pytest.mark.asyncio
async def test_poll_returns_work_response_when_present() -> None:
    work_payload = {
        'id': 'work-1',
        'type': 'work',
        'environment_id': 'env-1',
        'state': 'pending',
        'data': {'type': 'session', 'id': 'sess-1'},
        'secret': 'base64stuff',
        'created_at': '2026-05-23T00:00:00Z',
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=work_payload)

    client = _make_client(handler)
    out = await client.poll_for_work('env-1', 'env-sec')
    assert out is not None
    assert out['id'] == 'work-1'
    assert out['data']['id'] == 'sess-1'


@pytest.mark.asyncio
async def test_poll_passes_reclaim_param() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['url'] = str(req.url)
        return httpx.Response(200, json=None)

    client = _make_client(handler)
    await client.poll_for_work('env-1', 'env-sec', reclaim_older_than_ms=5000)
    assert 'reclaim_older_than_ms=5000' in seen['url']


@pytest.mark.asyncio
async def test_poll_omits_reclaim_param_when_none() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['url'] = str(req.url)
        return httpx.Response(200, json=None)

    client = _make_client(handler)
    await client.poll_for_work('env-1', 'env-sec')
    assert 'reclaim_older_than_ms' not in seen['url']


@pytest.mark.asyncio
async def test_poll_uses_environment_secret_not_oauth_token() -> None:
    """Poll auths with the env secret, NOT the OAuth token."""
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['auth'] = req.headers.get('authorization')
        return httpx.Response(200, json=None)

    client = _make_client(handler, get_access_token=lambda: 'oauth-tok')
    await client.poll_for_work('env-1', 'env-secret-xyz')
    # poll uses env secret directly — no oauth-tok in header
    assert seen['auth'] == 'Bearer env-secret-xyz'


@pytest.mark.asyncio
async def test_poll_validates_environment_id() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise AssertionError('should not reach network')

    client = _make_client(handler)
    with pytest.raises(ValueError):
        await client.poll_for_work('../bad', 'sec')


# ── acknowledge_work / stop_work / heartbeat_work ────────────────────────


@pytest.mark.asyncio
async def test_ack_validates_and_posts() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['url'] = str(req.url)
        seen['method'] = req.method
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.acknowledge_work('env-1', 'work-1', 'session-tok')
    assert seen['method'] == 'POST'
    assert seen['url'].endswith('/v1/environments/env-1/work/work-1/ack')


@pytest.mark.asyncio
async def test_stop_work_sends_force_field() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(req.content)
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.stop_work('env-1', 'work-1', force=True)
    assert seen['body'] == {'force': True}


@pytest.mark.asyncio
async def test_heartbeat_returns_dict() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'lease_extended': True,
            'state': 'running',
            'last_heartbeat': '2026-05-23T00:00:00Z',
            'ttl_seconds': 60,
        })

    client = _make_client(handler)
    out = await client.heartbeat_work('env-1', 'work-1', 'sess-tok')
    assert out['lease_extended'] is True
    assert out['state'] == 'running'


@pytest.mark.asyncio
async def test_heartbeat_malformed_raises_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'not-json')

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError, match='malformed'):
        await client.heartbeat_work('env-1', 'work-1', 'sess-tok')


# ── deregister_environment ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deregister_sends_delete() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['method'] = req.method
        seen['url'] = str(req.url)
        return httpx.Response(204)

    client = _make_client(handler)
    await client.deregister_environment('env-1')
    assert seen['method'] == 'DELETE'
    assert seen['url'].endswith('/v1/environments/bridge/env-1')


# ── archive_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_session_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.archive_session('sess-1')


@pytest.mark.asyncio
async def test_archive_session_409_is_success() -> None:
    """409 → already archived → idempotent success, no raise."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={'message': 'already archived'})

    client = _make_client(handler)
    # Must not raise.
    await client.archive_session('sess-1')


@pytest.mark.asyncio
async def test_archive_session_404_raises_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'message': 'not found'})

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError) as exc:
        await client.archive_session('sess-1')
    assert exc.value.status == 404


# ── reconnect_session ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconnect_session_sends_session_id_field() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(req.content)
        return httpx.Response(200, json={})

    client = _make_client(handler)
    await client.reconnect_session('env-1', 'sess-1')
    assert seen['body'] == {'session_id': 'sess-1'}


# ── send_permission_response_event ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_permission_response_event_wraps_in_events_array() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['body'] = json.loads(req.content)
        return httpx.Response(200, json={})

    client = _make_client(handler)
    event = {
        'type': 'control_response',
        'response': {
            'subtype': 'success',
            'request_id': 'req-1',
            'response': {'behavior': 'allow'},
        },
    }
    await client.send_permission_response_event('sess-1', event, 'sess-tok')
    assert seen['body'] == {'events': [event]}


# ── 403 expired-vs-permission branching ──────────────────────────────────


@pytest.mark.asyncio
async def test_403_with_expired_error_type_uses_expiry_message() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={'error': {'type': 'session_expired'}})

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError) as exc:
        await client.stop_work('env-1', 'work-1', force=False)
    assert exc.value.status == 403
    assert exc.value.error_type == 'session_expired'
    assert 'expired' in str(exc.value).lower()


@pytest.mark.asyncio
async def test_403_without_expired_error_type_uses_permission_message() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={'error': {'type': 'forbidden'}, 'message': 'denied'}
        )

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError) as exc:
        await client.stop_work('env-1', 'work-1', force=False)
    assert 'Access denied' in str(exc.value)
    assert 'denied' in str(exc.value)


# ── Empty-poll throttling ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_poll_logs_first_then_every_100th() -> None:
    """Verify that the consecutive-empty-poll counter triggers debug logs only on the
    first poll and every 100th — not after every individual empty poll."""
    logs: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=None)

    client = create_bridge_api_client(
        base_url='https://api.example.com',
        get_access_token=lambda: 'tok-1',
        runner_version='py-test-0.1',
        on_debug=lambda msg: logs.append(msg),
        client=_mock_client(handler),
    )

    for _ in range(101):
        await client.poll_for_work('env-1', 'env-sec')

    # Counter-related debug messages: poll 1 and poll 100 only.
    empty_logs = [m for m in logs if 'consecutive empty polls' in m]
    assert len(empty_logs) == 2
    assert 'no work, 1 consecutive' in empty_logs[0]
    assert 'no work, 100 consecutive' in empty_logs[1]

    # Pin the 101st-poll behavior: the count keeps incrementing but no
    # additional log fires (guards against an off-by-one modulo bug
    # that would otherwise also pass the two assertions above).
    await client.poll_for_work('env-1', 'env-sec')
    await client.poll_for_work('env-1', 'env-sec')
    empty_logs_after = [m for m in logs if 'consecutive empty polls' in m]
    assert len(empty_logs_after) == 2


@pytest.mark.asyncio
async def test_poll_response_resets_empty_streak() -> None:
    """A non-empty poll between empty ones resets the counter."""
    logs: list[str] = []

    responses = iter([
        httpx.Response(200, json=None),
        httpx.Response(200, json={
            'id': 'w-1',
            'type': 'work',
            'environment_id': 'e',
            'state': 'pending',
            'data': {'type': 'session', 'id': 's-1'},
            'secret': 'b64',
            'created_at': '2026-05-23T00:00:00Z',
        }),
        httpx.Response(200, json=None),  # back to empty
    ])

    def handler(req: httpx.Request) -> httpx.Response:
        return next(responses)

    client = create_bridge_api_client(
        base_url='https://api.example.com',
        get_access_token=lambda: 'tok-1',
        runner_version='py-test-0.1',
        on_debug=lambda msg: logs.append(msg),
        client=_mock_client(handler),
    )
    await client.poll_for_work('env-1', 'sec')
    await client.poll_for_work('env-1', 'sec')
    await client.poll_for_work('env-1', 'sec')

    empty_logs = [m for m in logs if 'consecutive empty polls' in m]
    # First empty (counter→1), then non-empty (resets), then empty (counter→1 again).
    assert len(empty_logs) == 2
    assert 'no work, 1 consecutive' in empty_logs[0]
    assert 'no work, 1 consecutive' in empty_logs[1]


# ── base_url normalization ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_non_json_body_raises_fatal() -> None:
    """200 with HTML/text body must surface as BridgeFatalError, not AttributeError.

    Regression test per CRITIC blocking fix. Realistic scenarios: gateway
    returns an HTML 200 (auth pages, CDN errors with 200), truncated
    response, content-type mismatch.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'<html>maintenance</html>')

    client = _make_client(handler)
    with pytest.raises(BridgeFatalError, match='non-JSON body'):
        await client.register_bridge_environment(_make_config())


@pytest.mark.asyncio
async def test_poll_empty_dict_falls_through_to_work_response() -> None:
    """Per CRITIC fix: `{}` is NOT no-work — only `null` body is.

    A server-issued `{}` would surface as a (malformed) WorkResponse so
    the orchestrator can detect server-contract violations rather than
    silently treating them as 'no work available'.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _make_client(handler)
    out = await client.poll_for_work('env-1', 'env-sec')
    # The {} body falls through and is returned as-is (not None).
    assert out == {}


@pytest.mark.asyncio
async def test_register_401_logs_no_refresh_handler() -> None:
    """When ``on_auth_401`` is unset, the 401 path emits a diagnostic log."""
    logs: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={'error': {'type': 'unauthorized'}})

    client = create_bridge_api_client(
        base_url='https://api.example.com',
        get_access_token=lambda: 'tok-1',
        runner_version='py-test-0.1',
        on_debug=lambda msg: logs.append(msg),
        client=_mock_client(handler),
    )
    with pytest.raises(BridgeFatalError):
        await client.register_bridge_environment(_make_config())
    assert any('no refresh handler' in m for m in logs)


@pytest.mark.asyncio
async def test_register_redacts_environment_secret_in_debug_log() -> None:
    """Debug logs must not leak environment_secret unredacted.

    Security regression guard — ``debug_body`` redacts ``environment_secret``
    via the centralized SECRET_FIELD_NAMES list in ``debug_utils``.
    """
    logs: list[str] = []
    long_secret = 'super-secret-environment-key-do-not-leak-12345'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'environment_id': 'e',
            'environment_secret': long_secret,
        })

    client = create_bridge_api_client(
        base_url='https://api.example.com',
        get_access_token=lambda: 'tok-1',
        runner_version='py-test-0.1',
        on_debug=lambda msg: logs.append(msg),
        client=_mock_client(handler),
    )
    await client.register_bridge_environment(_make_config())
    # Secret must appear redacted in any log, never in full.
    joined = '\n'.join(logs)
    assert long_secret not in joined, 'environment_secret leaked unredacted'


@pytest.mark.asyncio
async def test_httpx_connect_error_propagates() -> None:
    """Network errors (httpx.ConnectError) propagate unwrapped to the caller.

    No silent swallowing of network failures — the orchestrator's poll
    loop relies on seeing these so its backoff logic can fire.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused')

    client = _make_client(handler)
    with pytest.raises(httpx.ConnectError):
        await client.poll_for_work('env-1', 'env-sec')


@pytest.mark.asyncio
async def test_httpx_timeout_error_propagates() -> None:
    """``httpx.TimeoutException`` propagates the same way."""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout('timed out')

    client = _make_client(handler)
    with pytest.raises(httpx.ReadTimeout):
        await client.poll_for_work('env-1', 'env-sec')


@pytest.mark.asyncio
async def test_poll_with_pre_aborted_cancel_event_still_completes() -> None:
    """The ``cancel_event`` parameter is currently a no-op (Phase 5 wires it).

    Pins the "ignored for now" contract — if Phase 5 wiring is forgotten,
    this test still passes (event ignored), but a future regression that
    starts honoring it should add a new test that REPLACES this one.
    """
    import asyncio

    triggered = asyncio.Event()
    triggered.set()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=None)

    client = _make_client(handler)
    # Despite the pre-triggered event, the call completes normally.
    out = await client.poll_for_work('env-1', 'env-sec', cancel_event=triggered)
    assert out is None


@pytest.mark.asyncio
async def test_no_injected_client_path_uses_fresh_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory's no-injected-client fallback creates an AsyncClient per request.

    Regression test per CRITIC MAJOR: previously the production path
    (``client=None``) had zero test coverage. Monkeypatches the
    ``_send_with_fresh_client`` seam to record invocation and serve a
    canned response without going to the network.
    """
    calls: list[tuple[str, str]] = []

    async def fake_send(self, method, url, kwargs):  # type: ignore[no-untyped-def]
        calls.append((method, url))
        return httpx.Response(200, json={
            'environment_id': 'e-fresh', 'environment_secret': 's-fresh',
        })

    monkeypatch.setattr(
        bridge_api._BridgeApiClient,
        '_send_with_fresh_client',
        fake_send,
    )

    client = create_bridge_api_client(
        base_url='https://api.example.com',
        get_access_token=lambda: 'tok-1',
        runner_version='py-test-0.1',
        # client= deliberately omitted to exercise the fresh-client path
    )
    out = await client.register_bridge_environment(_make_config())
    assert out == {'environment_id': 'e-fresh', 'environment_secret': 's-fresh'}
    assert calls == [('POST', 'https://api.example.com/v1/environments/bridge')]


@pytest.mark.asyncio
async def test_base_url_trailing_slash_is_stripped() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['url'] = str(req.url)
        return httpx.Response(200, json={
            'environment_id': 'e', 'environment_secret': 's'
        })

    client = _make_client(handler, base_url='https://api.example.com/')
    await client.register_bridge_environment(_make_config())
    # No double slashes in the URL.
    assert '//v1/' not in seen['url']
    assert seen['url'] == 'https://api.example.com/v1/environments/bridge'
