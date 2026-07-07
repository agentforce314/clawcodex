"""Tests for ``src.bridge.inbound_attachments``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.bridge.inbound_attachments import (
    extract_inbound_attachments,
    prepend_path_refs,
    resolve_and_prepend,
    resolve_inbound_attachments,
)


# ── extract_inbound_attachments ──────────────────────────────────────────


def test_extract_returns_empty_for_non_dict() -> None:
    assert extract_inbound_attachments(None) == []
    assert extract_inbound_attachments('string') == []
    assert extract_inbound_attachments(42) == []


def test_extract_returns_empty_for_missing_field() -> None:
    assert extract_inbound_attachments({}) == []
    assert extract_inbound_attachments({'other': 'value'}) == []


def test_extract_returns_empty_for_non_list_attachments() -> None:
    assert extract_inbound_attachments({'file_attachments': 'not a list'}) == []
    assert extract_inbound_attachments({'file_attachments': None}) == []


def test_extract_returns_well_formed_list() -> None:
    msg = {
        'file_attachments': [
            {'file_uuid': 'abc-123', 'file_name': 'foo.png'},
            {'file_uuid': 'def-456', 'file_name': 'bar.txt'},
        ],
    }
    out = extract_inbound_attachments(msg)
    assert out == [
        {'file_uuid': 'abc-123', 'file_name': 'foo.png'},
        {'file_uuid': 'def-456', 'file_name': 'bar.txt'},
    ]


def test_extract_skips_malformed_entries() -> None:
    """Items without both file_uuid and file_name are dropped."""
    msg = {
        'file_attachments': [
            {'file_uuid': 'a', 'file_name': 'ok.png'},
            {'file_uuid': 'b'},  # no file_name → dropped
            'not a dict',         # not a dict → dropped
            {'file_name': 'c'},   # no file_uuid → dropped
            {'file_uuid': 123, 'file_name': 'wrong-type'},  # non-string uuid → dropped
        ],
    }
    out = extract_inbound_attachments(msg)
    assert out == [{'file_uuid': 'a', 'file_name': 'ok.png'}]


# ── prepend_path_refs ────────────────────────────────────────────────────


def test_prepend_noop_when_empty_prefix() -> None:
    """Empty prefix returns the same content reference."""
    s = 'hello'
    assert prepend_path_refs(s, '') is s
    blocks = [{'type': 'text', 'text': 'hi'}]
    assert prepend_path_refs(blocks, '') is blocks


def test_prepend_string_content_concatenates() -> None:
    assert prepend_path_refs('hello', '@"/tmp/foo.png" ') == '@"/tmp/foo.png" hello'


def test_prepend_targets_last_text_block_in_list() -> None:
    """When content is a list, the last text block gets the prefix."""
    blocks = [
        {'type': 'text', 'text': 'first text'},
        {'type': 'image', 'source': {}},
        {'type': 'text', 'text': 'last text'},
    ]
    out = prepend_path_refs(blocks, '@"/tmp/x.png" ')
    assert out[0] == {'type': 'text', 'text': 'first text'}
    assert out[1] == {'type': 'image', 'source': {}}
    assert out[2] == {'type': 'text', 'text': '@"/tmp/x.png" last text'}


def test_prepend_appends_text_block_when_none_exists() -> None:
    """List with no text block → append one (trailing space stripped)."""
    blocks = [{'type': 'image', 'source': {}}]
    out = prepend_path_refs(blocks, '@"/tmp/x.png" ')
    assert out == [
        {'type': 'image', 'source': {}},
        {'type': 'text', 'text': '@"/tmp/x.png"'},
    ]


def test_prepend_non_string_non_list_returns_unchanged() -> None:
    """Unrecognized content shapes pass through (defensive)."""
    assert prepend_path_refs(123, '@"foo" ') == 123


# ── resolve_inbound_attachments + _resolve_one ───────────────────────────


@pytest.fixture
def _isolated_uploads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin uploads to a temp dir so tests don't touch ~/.clawcodex/uploads."""
    monkeypatch.setenv('CLAWCODEX_CONFIG_DIR', str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_resolve_returns_empty_for_no_attachments() -> None:
    assert await resolve_inbound_attachments([]) == ''


@pytest.mark.asyncio
async def test_resolve_returns_empty_when_no_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No CLAUDE_BRIDGE_OAUTH_TOKEN → skip all resolution → empty prefix."""
    # Strip any override.
    monkeypatch.delenv('CLAUDE_BRIDGE_OAUTH_TOKEN', raising=False)
    # Force get_bridge_access_token to return None.
    with patch(
        'src.bridge.inbound_attachments.get_bridge_access_token',
        return_value=None,
    ):
        out = await resolve_inbound_attachments(
            [{'file_uuid': 'a', 'file_name': 'foo.png'}],
        )
    assert out == ''


@pytest.mark.asyncio
async def test_resolve_writes_attachment_and_returns_at_path(
    _isolated_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: fetches via httpx, writes to disk, returns @"path" prefix."""
    monkeypatch.setenv('CLAUDE_BRIDGE_OAUTH_TOKEN', 'tok-abc')

    expected_content = b'PNG bytes here'
    fetched_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        fetched_urls.append(str(req.url))
        return httpx.Response(200, content=expected_content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await resolve_inbound_attachments(
            [{'file_uuid': 'abc-12345', 'file_name': 'my image.png'}],
            http_client=client,
        )

    # Returned prefix is the quoted @path form with trailing space.
    assert out.startswith('@"')
    assert out.endswith('" ')
    # The file was actually written.
    assert len(fetched_urls) == 1
    assert '/api/oauth/files/abc-12345/content' in fetched_urls[0]
    # File on disk under the isolated uploads dir.
    # Output is `@"<path>" ` — strip leading `@"` and trailing `" `.
    path_in_prefix = out[2:-2]
    written = Path(path_in_prefix)
    assert written.exists()
    assert written.read_bytes() == expected_content
    # Filename was sanitized (space → underscore).
    assert ' ' not in written.name


@pytest.mark.asyncio
async def test_resolve_skips_failed_fetches(
    _isolated_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-200 responses are skipped, not raised. Returns empty when all fail."""
    monkeypatch.setenv('CLAUDE_BRIDGE_OAUTH_TOKEN', 'tok-abc')

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'error': 'not found'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await resolve_inbound_attachments(
            [{'file_uuid': 'a', 'file_name': 'foo.png'}],
            http_client=client,
        )
    assert out == ''


@pytest.mark.asyncio
async def test_resolve_swallows_network_errors(
    _isolated_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('CLAUDE_BRIDGE_OAUTH_TOKEN', 'tok-abc')

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('boom')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await resolve_inbound_attachments(
            [{'file_uuid': 'a', 'file_name': 'foo.png'}],
            http_client=client,
        )
    # Network error → skip → empty prefix.
    assert out == ''


@pytest.mark.asyncio
async def test_resolve_handles_mixed_success_and_failure(
    _isolated_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When some attachments resolve and others fail, only successful ones appear."""
    monkeypatch.setenv('CLAUDE_BRIDGE_OAUTH_TOKEN', 'tok-abc')

    def handler(req: httpx.Request) -> httpx.Response:
        # Only 'good-uuid' resolves; 'bad-uuid' 404s.
        if 'good-uuid' in str(req.url):
            return httpx.Response(200, content=b'data')
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await resolve_inbound_attachments(
            [
                {'file_uuid': 'bad-uuid', 'file_name': 'bad.png'},
                {'file_uuid': 'good-uuid', 'file_name': 'good.png'},
            ],
            http_client=client,
        )
    # Only good-uuid in the prefix.
    assert 'good.png' in out
    assert 'bad.png' not in out


# ── resolve_and_prepend (the convenience function) ──────────────────────


@pytest.mark.asyncio
async def test_resolve_and_prepend_noop_for_no_attachments() -> None:
    """Returns content unchanged when message has no file_attachments."""
    content = 'hello'
    out = await resolve_and_prepend({'no_attachments': True}, content)
    assert out is content


@pytest.mark.asyncio
async def test_resolve_and_prepend_end_to_end(
    _isolated_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('CLAUDE_BRIDGE_OAUTH_TOKEN', 'tok-abc')

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'data')

    msg = {
        'file_attachments': [
            {'file_uuid': 'uuid-1', 'file_name': 'foo.png'},
        ],
    }
    content = 'look at this image'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await resolve_and_prepend(msg, content, http_client=client)
    # Prefix was prepended.
    assert isinstance(out, str)
    assert out.endswith('look at this image')
    assert out.startswith('@"')
    assert 'foo.png' in out
