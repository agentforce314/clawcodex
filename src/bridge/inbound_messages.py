"""Process inbound user messages from the bridge.

Ports ``typescript/src/bridge/inboundMessages.ts``.

Two responsibilities:

1. ``extract_inbound_message_fields`` — pull (content, uuid) off a parsed
   SDKMessage of type ``user`` for enqueueing. Returns ``None`` when the
   message should be skipped (non-user, missing/empty content).

2. ``normalize_image_blocks`` — fix camelCase ``mediaType`` → snake_case
   ``media_type`` on image blocks from bridge clients (iOS/web composer).
   Returns the original list when no normalization is needed (zero-alloc
   happy path).

The TS file uses ``detectImageFormatFromBase64`` from ``utils/imageResizer.js``;
the Python port inlines a small magic-byte sniffer since the only formats
needed are PNG / JPEG / GIF / WebP.
"""

from __future__ import annotations

import base64
from typing import Any


def detect_image_format_from_base64(data: str) -> str:
    """Sniff PNG / JPEG / GIF / WebP from the first 12 base64-decoded bytes.

    Inlined substitute for TS ``utils/imageResizer.detectImageFormatFromBase64``.
    Returns the Anthropic ``media_type`` string. Falls back to ``image/png``
    when the format cannot be identified (matches what the upstream API
    would default to and avoids breaking the request on a slightly-malformed
    payload).
    """
    try:
        decoded = base64.b64decode(data[:24])  # 24 base64 chars ≈ 16 bytes
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return 'image/png'

    if len(decoded) < 4:
        return 'image/png'
    # PNG: 89 50 4E 47
    if decoded[:4] == b'\x89PNG':
        return 'image/png'
    # JPEG: FF D8 FF
    if decoded[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    # GIF: 47 49 46 38 (GIF8)
    if decoded[:4] == b'GIF8':
        return 'image/gif'
    # WebP: RIFF + WEBP at offset 8
    if len(decoded) >= 12 and decoded[:4] == b'RIFF' and decoded[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/png'


def extract_inbound_message_fields(
    msg: dict[str, Any],
) -> tuple[Any, str | None] | None:
    """Extract (content, uuid) from a parsed user SDKMessage, or None to skip.

    Mirrors TS ``extractInboundMessageFields`` on ``inboundMessages.ts:21-40``.
    Returns ``None`` for non-user messages and for empty content (which is
    also what the TS function returns via ``undefined``).

    **Return shape divergence from TS**: TS returns an object
    ``{ content, uuid }``; Python returns a tuple ``(content, uuid)`` —
    matches Python idiom. Phase 5+ orchestrator porters should unpack the
    tuple, not destructure as if it were a dict.

    Image content blocks are normalized as a side effect of returning them
    via ``normalize_image_blocks``.
    """
    if msg.get('type') != 'user':
        return None
    inner = msg.get('message') or {}
    content = inner.get('content')
    if not content:
        return None
    if isinstance(content, list) and len(content) == 0:
        return None

    raw_uuid = msg.get('uuid')
    uuid_str = raw_uuid if isinstance(raw_uuid, str) else None

    if isinstance(content, list):
        return normalize_image_blocks(content), uuid_str
    return content, uuid_str


def normalize_image_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix malformed image source blocks. Returns input ref on the happy path.

    Mirrors TS ``normalizeImageBlocks`` on ``inboundMessages.ts:52-73``.
    Bridge clients may send ``mediaType`` (camelCase) instead of
    ``media_type`` (snake_case), or omit it entirely. Without normalization
    the upstream API rejects the request with "media_type: Field required".

    Fast-path: scan blocks; if none are malformed, return the input list
    reference unchanged (zero allocation).
    """
    if not any(_is_malformed_base64_image(b) for b in blocks):
        return blocks

    out: list[dict[str, Any]] = []
    for block in blocks:
        if not _is_malformed_base64_image(block):
            out.append(block)
            continue
        source = block.get('source') or {}
        camel = source.get('mediaType')
        media_type: str
        if isinstance(camel, str) and camel:
            media_type = camel
        else:
            data = source.get('data', '')
            data_str = data if isinstance(data, str) else ''
            media_type = detect_image_format_from_base64(data_str)
        new_source = {
            'type': 'base64',
            'media_type': media_type,
            'data': source.get('data', ''),
        }
        out.append({**block, 'source': new_source})
    return out


def _is_malformed_base64_image(block: dict[str, Any]) -> bool:
    """A base64 image block missing the snake_case ``media_type`` field."""
    if block.get('type') != 'image':
        return False
    source = block.get('source') or {}
    if source.get('type') != 'base64':
        return False
    return 'media_type' not in source


__all__ = [
    'detect_image_format_from_base64',
    'extract_inbound_message_fields',
    'normalize_image_blocks',
]
