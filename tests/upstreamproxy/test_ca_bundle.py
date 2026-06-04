"""Tests for ``src.upstreamproxy.ca_bundle``.

Mirrors ``typescript/src/upstreamproxy/upstreamproxy.test.ts:1-43``
plus the ``download_ca_bundle`` happy path + failure cases.
"""

from __future__ import annotations

import httpx
import pytest

from src.upstreamproxy.ca_bundle import download_ca_bundle, is_valid_pem_content

# ─── is_valid_pem_content (replicates upstreamproxy.test.ts:1-43) ────────


def test_pem_single_block() -> None:
    pem = '\n'.join([
        '-----BEGIN CERTIFICATE-----',
        'MIICpDCCAYwCCQDU+pQ4pHgSpDANBgkqhkiG9w0BAQsFADAUMRIwEAYDVQQDDAls',
        'b2NhbGhvc3QwHhcNMjMwMTAxMDAwMDAwWhcNMjQwMTAxMDAwMDAwWjAUMRIwEAYD',
        'VQQDDAlsb2NhbGhvc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQC7',
        '-----END CERTIFICATE-----',
    ])
    assert is_valid_pem_content(pem)


def test_pem_multiple_blocks() -> None:
    block = '-----BEGIN CERTIFICATE-----\nABCD\n-----END CERTIFICATE-----'
    assert is_valid_pem_content(f'{block}\n{block}')


def test_arbitrary_text_rejected() -> None:
    assert not is_valid_pem_content('Hello world')
    assert not is_valid_pem_content('<html><body>error</body></html>')
    assert not is_valid_pem_content('{"error":"unauthorized"}')


def test_empty_string_rejected() -> None:
    assert not is_valid_pem_content('')


def test_whitespace_only_rejected() -> None:
    assert not is_valid_pem_content('   \n   ')


def test_malformed_pem_no_end_marker_rejected() -> None:
    assert not is_valid_pem_content('-----BEGIN CERTIFICATE-----\nABCD')


def test_bytes_input_accepted() -> None:
    """is_valid_pem_content accepts both str and bytes."""
    pem_bytes = b'-----BEGIN CERTIFICATE-----\nABCD\n-----END CERTIFICATE-----'
    assert is_valid_pem_content(pem_bytes)
    assert not is_valid_pem_content(b'not pem')
    assert not is_valid_pem_content(b'')


# ─── download_ca_bundle ──────────────────────────────────────────────────


_GOOD_PEM = (
    b'-----BEGIN CERTIFICATE-----\n'
    b'MIICpDCCAYwCCQDU+pQ4pHgSpDANBgkqhkiG9w0BAQsFADAUMRIwEAYDVQQDDAls\n'
    b'-----END CERTIFICATE-----\n'
)


@pytest.mark.asyncio
async def test_happy_path_writes_concatenated_bundle(tmp_path):
    """200 OK with PEM body → file written = system + CCR PEM."""
    system_ca = tmp_path / 'system.crt'
    system_ca.write_bytes(b'SYSTEM_CA_BYTES\n')
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/code/upstreamproxy/ca-cert'
        return httpx.Response(200, content=_GOOD_PEM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle('https://api.test', system_ca, out, client=client)

    assert ok is True
    written = out.read_bytes()
    assert b'SYSTEM_CA_BYTES' in written
    assert _GOOD_PEM in written
    # System CA comes BEFORE the CCR PEM (per upstreamproxy.ts:295).
    assert written.index(b'SYSTEM_CA_BYTES') < written.index(_GOOD_PEM)


@pytest.mark.asyncio
async def test_missing_system_ca_falls_through(tmp_path):
    """System CA file missing — still works, writes only the CCR PEM."""
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_GOOD_PEM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle(
            'https://api.test', tmp_path / 'nonexistent.crt', out, client=client
        )
    assert ok is True
    assert _GOOD_PEM in out.read_bytes()


@pytest.mark.asyncio
async def test_non_2xx_returns_false(tmp_path):
    """500 from server → fail-open, returns False, no file written."""
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b'oops')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle(
            'https://api.test', tmp_path / 'sys.crt', out, client=client
        )
    assert ok is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_non_pem_response_returns_false(tmp_path):
    """200 OK but body is HTML — refuse to write, fail open."""
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'<html>not a cert</html>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle(
            'https://api.test', tmp_path / 'sys.crt', out, client=client
        )
    assert ok is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_network_error_returns_false(tmp_path):
    """Connection error (e.g., DNS fail, refused) → fail open."""
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle(
            'https://api.test', tmp_path / 'sys.crt', out, client=client
        )
    assert ok is False


@pytest.mark.asyncio
async def test_atomic_write_no_partial_file_on_crash(tmp_path, monkeypatch):
    """Simulate fsync failure: the .tmp file should not survive."""
    out = tmp_path / 'merged.crt'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_GOOD_PEM)

    # Force os.replace to fail after the temp file is written.
    import os as _os

    original_replace = _os.replace

    def fail_replace(*args, **kwargs):
        raise OSError('simulated rename failure')

    monkeypatch.setattr(_os, 'replace', fail_replace)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await download_ca_bundle(
            'https://api.test', tmp_path / 'sys.crt', out, client=client
        )

    assert ok is False
    assert not out.exists()
    # No .tmp file should be left behind.
    leftover = list(tmp_path.glob('.ca-bundle.*.tmp'))
    assert leftover == [], f'expected no leftover tempfile, got {leftover}'

    # Restore for subsequent tests.
    monkeypatch.setattr(_os, 'replace', original_replace)
