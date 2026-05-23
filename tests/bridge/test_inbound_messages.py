"""Tests for ``src.bridge.inbound_messages``."""

from __future__ import annotations

import base64

import pytest

from src.bridge.inbound_messages import (
    detect_image_format_from_base64,
    extract_inbound_message_fields,
    normalize_image_blocks,
)


# ---------------------------------------------------------------------------
# detect_image_format_from_base64
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def test_detect_png() -> None:
    assert detect_image_format_from_base64(_b64(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8)) == 'image/png'


def test_detect_jpeg() -> None:
    assert detect_image_format_from_base64(_b64(b'\xff\xd8\xff\xe0' + b'\x00' * 8)) == 'image/jpeg'


def test_detect_gif() -> None:
    assert detect_image_format_from_base64(_b64(b'GIF89a' + b'\x00' * 8)) == 'image/gif'


def test_detect_webp() -> None:
    assert (
        detect_image_format_from_base64(_b64(b'RIFF\x00\x00\x00\x00WEBPVP8 '))
        == 'image/webp'
    )


def test_detect_unknown_defaults_to_png() -> None:
    assert detect_image_format_from_base64(_b64(b'\x00\x00\x00\x00' + b'\x00' * 8)) == 'image/png'


def test_detect_malformed_base64_defaults_to_png() -> None:
    assert detect_image_format_from_base64('not!valid!base64') == 'image/png'


# ---------------------------------------------------------------------------
# extract_inbound_message_fields
# ---------------------------------------------------------------------------


def test_extract_returns_none_for_non_user() -> None:
    assert extract_inbound_message_fields({'type': 'assistant', 'message': {'content': 'hi'}}) is None


def test_extract_returns_none_for_missing_content() -> None:
    assert extract_inbound_message_fields({'type': 'user', 'message': {}}) is None
    assert extract_inbound_message_fields({'type': 'user'}) is None


def test_extract_returns_none_for_empty_array_content() -> None:
    msg = {'type': 'user', 'message': {'content': []}}
    assert extract_inbound_message_fields(msg) is None


def test_extract_returns_string_content() -> None:
    msg = {'type': 'user', 'message': {'content': 'hello'}, 'uuid': 'u-1'}
    out = extract_inbound_message_fields(msg)
    assert out == ('hello', 'u-1')


def test_extract_returns_none_uuid_when_missing() -> None:
    msg = {'type': 'user', 'message': {'content': 'hello'}}
    out = extract_inbound_message_fields(msg)
    assert out == ('hello', None)


def test_extract_returns_list_content_normalized() -> None:
    blocks = [{'type': 'text', 'text': 'hi'}]
    msg = {'type': 'user', 'message': {'content': blocks}, 'uuid': 'u-2'}
    out = extract_inbound_message_fields(msg)
    assert out is not None
    assert out[0] == blocks
    assert out[1] == 'u-2'


# ---------------------------------------------------------------------------
# normalize_image_blocks (the hot path of the file)
# ---------------------------------------------------------------------------


def test_well_formed_blocks_returned_by_reference() -> None:
    """Happy path: same list object returned (zero-allocation)."""
    blocks = [
        {'type': 'text', 'text': 'hi'},
        {
            'type': 'image',
            'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'abc'},
        },
    ]
    out = normalize_image_blocks(blocks)
    assert out is blocks


def test_camel_case_mediatype_normalized_to_snake() -> None:
    blocks = [
        {
            'type': 'image',
            'source': {'type': 'base64', 'mediaType': 'image/jpeg', 'data': 'abc'},
        },
    ]
    out = normalize_image_blocks(blocks)
    assert out is not blocks
    assert out[0]['source']['media_type'] == 'image/jpeg'
    # The new source dict has only type/media_type/data; the old camelCase
    # field must be absent (not just hidden behind a same-value alias).
    assert 'mediaType' not in out[0]['source']
    assert out[0]['source']['type'] == 'base64'
    assert out[0]['source']['data'] == 'abc'


def test_missing_media_type_derived_from_base64() -> None:
    """If neither mediaType nor media_type is set, sniff from base64 data."""
    png_data = _b64(b'\x89PNG' + b'\x00' * 12)
    blocks = [
        {
            'type': 'image',
            'source': {'type': 'base64', 'data': png_data},
        },
    ]
    out = normalize_image_blocks(blocks)
    assert out[0]['source']['media_type'] == 'image/png'


def test_text_blocks_preserved_in_mixed_content() -> None:
    blocks = [
        {'type': 'text', 'text': 'desc'},
        {
            'type': 'image',
            'source': {'type': 'base64', 'mediaType': 'image/png', 'data': 'd'},
        },
    ]
    out = normalize_image_blocks(blocks)
    assert out[0] == {'type': 'text', 'text': 'desc'}
    assert out[1]['source']['media_type'] == 'image/png'


def test_non_base64_source_not_touched() -> None:
    """URL-source images are unaffected by the snake_case fix."""
    blocks = [
        {
            'type': 'image',
            'source': {'type': 'url', 'url': 'https://x/y.png'},
        },
    ]
    out = normalize_image_blocks(blocks)
    assert out is blocks  # fast path: not malformed


def test_empty_blocks_returns_input() -> None:
    blocks: list[dict[str, object]] = []
    assert normalize_image_blocks(blocks) is blocks
