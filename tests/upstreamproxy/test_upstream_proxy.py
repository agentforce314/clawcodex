"""Tests for ``src.upstreamproxy.upstream_proxy``.

Covers the env-var gate logic, fail-open semantics, and
``get_upstream_proxy_env`` (both enabled and inherited paths).
The full init_upstream_proxy lifecycle is exercised via mocked HTTP +
mocked relay (we don't spin up a real WS server here — that's
``test_relay_e2e``).
"""

from __future__ import annotations

import httpx
import pytest

from src.upstreamproxy import upstream_proxy as up
from src.upstreamproxy.upstream_proxy import (
    UpstreamProxyState,
    _is_env_truthy,
    get_upstream_proxy_env,
    init_upstream_proxy,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Reset module-level state between tests; clear inherited env vars."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every relevant env var so test cases set them explicitly."""
    for var in (
        'CLAUDE_CODE_REMOTE',
        'CCR_UPSTREAM_PROXY_ENABLED',
        'CLAUDE_CODE_REMOTE_SESSION_ID',
        'ANTHROPIC_BASE_URL',
        'HTTPS_PROXY',
        'https_proxy',
        'NO_PROXY',
        'no_proxy',
        'SSL_CERT_FILE',
        'NODE_EXTRA_CA_CERTS',
        'REQUESTS_CA_BUNDLE',
        'CURL_CA_BUNDLE',
    ):
        monkeypatch.delenv(var, raising=False)


# ─── _is_env_truthy ──────────────────────────────────────────────────────


@pytest.mark.parametrize('val', ['1', 'true', 'TRUE', 'yes', 'on', 'anything-else'])
def test_truthy_values(val: str) -> None:
    assert _is_env_truthy(val)


@pytest.mark.parametrize('val', ['', '0', 'false', 'FALSE', 'no', 'off', None])
def test_falsy_values(val: str | None) -> None:
    assert not _is_env_truthy(val)


# ─── init_upstream_proxy gate logic ──────────────────────────────────────


@pytest.mark.asyncio
async def test_no_env_vars_returns_disabled(_clean_env) -> None:
    state = await init_upstream_proxy()
    assert state.enabled is False
    assert state.port is None


@pytest.mark.asyncio
async def test_only_first_env_set_returns_disabled(
    _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CLAUDE_CODE_REMOTE`` alone is insufficient; need both gates."""
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    state = await init_upstream_proxy()
    assert state.enabled is False


@pytest.mark.asyncio
async def test_only_second_env_set_returns_disabled(
    _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CCR_UPSTREAM_PROXY_ENABLED`` alone is insufficient."""
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    state = await init_upstream_proxy()
    assert state.enabled is False


@pytest.mark.asyncio
async def test_both_env_set_but_no_session_id_returns_disabled(
    _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both gates AND a session ID are needed."""
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    state = await init_upstream_proxy()
    assert state.enabled is False


@pytest.mark.asyncio
async def test_missing_token_file_returns_disabled(
    _clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """All env vars set but no token file — fail-open, return disabled."""
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE_SESSION_ID', 'cse_test')
    state = await init_upstream_proxy(token_path=tmp_path / 'no-such-file')
    assert state.enabled is False


@pytest.mark.asyncio
async def test_empty_token_file_returns_disabled(
    _clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Token file exists but contains only whitespace — fail-open."""
    token_path = tmp_path / 'token'
    token_path.write_text('   \n   ')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE_SESSION_ID', 'cse_test')
    state = await init_upstream_proxy(token_path=token_path)
    assert state.enabled is False


# ─── get_upstream_proxy_env ──────────────────────────────────────────────


def test_disabled_returns_empty_dict(_clean_env) -> None:
    """No state, no inherited proxy env → empty."""
    assert get_upstream_proxy_env() == {}


def test_disabled_inherits_parent_proxy_env(
    _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Child CLI: parent had HTTPS_PROXY + SSL_CERT_FILE → inherit."""
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9999')
    monkeypatch.setenv('https_proxy', 'http://127.0.0.1:9999')
    monkeypatch.setenv('SSL_CERT_FILE', '/tmp/parent-ca.crt')
    monkeypatch.setenv('NO_PROXY', 'localhost,*.anthropic.com')
    env = get_upstream_proxy_env()
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:9999'
    assert env['SSL_CERT_FILE'] == '/tmp/parent-ca.crt'
    assert env['NO_PROXY'] == 'localhost,*.anthropic.com'


def test_disabled_partial_parent_env_returns_empty(
    _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent has HTTPS_PROXY but no SSL_CERT_FILE — refuse to inherit."""
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9999')
    assert get_upstream_proxy_env() == {}


def test_enabled_returns_full_env_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """When state.enabled, return the 9-var recipe with right NO_PROXY order."""
    # Prime module state directly (bypass init for this unit test).
    up._state = UpstreamProxyState(
        enabled=True, port=12345, ca_bundle_path='/tmp/ca.crt'
    )
    env = get_upstream_proxy_env()
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:12345'
    assert env['https_proxy'] == 'http://127.0.0.1:12345'
    assert env['SSL_CERT_FILE'] == '/tmp/ca.crt'
    assert env['NODE_EXTRA_CA_CERTS'] == '/tmp/ca.crt'
    assert env['REQUESTS_CA_BUNDLE'] == '/tmp/ca.crt'
    assert env['CURL_CA_BUNDLE'] == '/tmp/ca.crt'
    # NO_PROXY: anthropic.com forms must appear in the right order.
    no_proxy = env['NO_PROXY']
    apex = no_proxy.index('anthropic.com')
    suffix = no_proxy.index('.anthropic.com')
    glob = no_proxy.index('*.anthropic.com')
    assert apex < suffix < glob


# ─── End-to-end happy path with mocked HTTP + mocked relay ──────────────


@pytest.mark.asyncio
async def test_init_happy_path_with_mocked_components(
    _clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """All gates pass + token file exists + CA download succeeds + relay starts."""
    token_path = tmp_path / 'token'
    token_path.write_text('secret-token-123')
    sys_ca = tmp_path / 'system.crt'
    sys_ca.write_bytes(b'SYS\n')
    out_ca = tmp_path / 'merged.crt'

    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE_SESSION_ID', 'cse_test')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://api.test')

    # Patch download_ca_bundle to write a fake bundle and return True.
    async def fake_download_ca_bundle(
        *, base_url, system_ca_path, out_path, **kwargs
    ) -> bool:
        out_path.write_bytes(b'-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----\n')
        return True

    monkeypatch.setattr(up, 'download_ca_bundle', fake_download_ca_bundle)

    # Patch start_upstream_proxy_relay to skip the real WS upgrade.
    class _FakeRelay:
        port = 54321

        async def stop(self) -> None:
            pass

    async def fake_start_relay(*, ws_url, session_id, token):
        assert ws_url == 'wss://api.test/v1/code/upstreamproxy/ws'
        assert session_id == 'cse_test'
        assert token == 'secret-token-123'
        return _FakeRelay()

    monkeypatch.setattr(up, 'start_upstream_proxy_relay', fake_start_relay)

    state = await init_upstream_proxy(
        token_path=token_path, system_ca_path=sys_ca, ca_bundle_path=out_ca
    )

    assert state.enabled is True
    assert state.port == 54321
    assert state.ca_bundle_path == str(out_ca)
    # Token file unlinked AFTER relay startup (per WI-5.5 step 5).
    assert not token_path.exists()
    # Env dict reflects the new state.
    env = get_upstream_proxy_env()
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:54321'


@pytest.mark.asyncio
async def test_init_relay_failure_is_fail_open(
    _clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Relay startup raises → return DISABLED, do NOT propagate."""
    token_path = tmp_path / 'token'
    token_path.write_text('tok')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE_SESSION_ID', 'cse_test')

    async def good_download(**kwargs) -> bool:
        return True

    async def bad_relay(**kwargs):
        raise OSError('cannot bind port')

    monkeypatch.setattr(up, 'download_ca_bundle', good_download)
    monkeypatch.setattr(up, 'start_upstream_proxy_relay', bad_relay)

    state = await init_upstream_proxy(token_path=token_path)
    assert state.enabled is False
    # Token file should still be present — relay didn't start, so the
    # unlink step is skipped (a supervisor restart can retry).
    assert token_path.exists()


@pytest.mark.asyncio
async def test_init_ca_download_failure_is_fail_open(
    _clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """CA download returns False → return DISABLED."""
    token_path = tmp_path / 'token'
    token_path.write_text('tok')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
    monkeypatch.setenv('CCR_UPSTREAM_PROXY_ENABLED', '1')
    monkeypatch.setenv('CLAUDE_CODE_REMOTE_SESSION_ID', 'cse_test')

    async def fail_download(**kwargs) -> bool:
        return False

    monkeypatch.setattr(up, 'download_ca_bundle', fail_download)

    state = await init_upstream_proxy(token_path=token_path)
    assert state.enabled is False
    assert token_path.exists(), 'token file must NOT be unlinked when CA download fails'
