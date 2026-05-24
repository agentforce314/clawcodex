"""Resolve ``file_uuid`` attachments on inbound bridge user messages.

Ports ``typescript/src/bridge/inboundAttachments.ts``.

Web composer uploads files via cookie-authed ``/api/{org}/upload``, then
sends ``file_uuid`` alongside the message. This module fetches each via
GET ``/api/oauth/files/{uuid}/content`` (OAuth-authed, same store),
writes to ``~/.claude/uploads/{sessionId}/``, and returns ``@path`` refs
the Read tool can pick up.

**Best-effort**: any failure (no token, network, non-2xx, disk) logs at
debug and skips that attachment. The message still reaches Claude, just
without the ``@path`` ref.

The Phase 10 caveat: ``get_bridge_access_token()`` returns ``None`` in
the Python build until Phase 10 wires the keychain OAuth read — so this
module degrades to "extract attachments + skip resolution" until then.
The shape of the module is correct; only the network fetch is gated.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from src.bootstrap.state import get_session_id
from src.bridge.bridge_config import get_bridge_access_token, get_bridge_base_url

logger = logging.getLogger(__name__)


_DOWNLOAD_TIMEOUT_SECONDS = 30.0


_SAFE_FILENAME_RE = re.compile(r'[^a-zA-Z0-9._-]')


# ── Public surface ────────────────────────────────────────────────────────


def extract_inbound_attachments(msg: Any) -> list[dict[str, str]]:
    """Pull ``file_attachments`` off a loosely-typed inbound message.

    Mirrors TS ``extractInboundAttachments`` on ``inboundAttachments.ts:42-48``.
    Returns a list of ``{file_uuid, file_name}`` dicts; empty when the
    message has no ``file_attachments`` field or the field is malformed.
    """
    if not isinstance(msg, dict):
        return []
    raw = msg.get('file_attachments')
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_uuid = item.get('file_uuid')
        file_name = item.get('file_name')
        if isinstance(file_uuid, str) and isinstance(file_name, str):
            out.append({'file_uuid': file_uuid, 'file_name': file_name})
    return out


async def resolve_inbound_attachments(
    attachments: list[dict[str, str]],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch every attachment, write to disk, return the ``@path`` ref prefix.

    Mirrors TS ``resolveInboundAttachments`` on
    ``inboundAttachments.ts:123-134``. Returns the empty string when
    nothing resolves (no auth token, all fetches failed, no
    attachments). Multiple successful resolutions are joined with
    spaces and end with a trailing space.

    The quoted ``@"/path/with spaces"`` form prevents the
    ``extractAtMentionedFiles`` consumer from truncating home dirs that
    contain spaces (e.g. ``/Users/John Smith/``).
    """
    if not attachments:
        return ''
    _debug(f'resolving {len(attachments)} attachment(s)')
    paths: list[str] = []
    for att in attachments:
        path = await _resolve_one(att, http_client=http_client)
        if path is not None:
            paths.append(path)
    if not paths:
        return ''
    return ' '.join(f'@"{p}"' for p in paths) + ' '


def prepend_path_refs(content: Any, prefix: str) -> Any:
    """Prepend ``@path`` refs to message content.

    Mirrors TS ``prependPathRefs`` on ``inboundAttachments.ts:142-161``.
    For string content: simple concatenation. For list content
    (mixed text + image): target the LAST text block so the consumer's
    ``processedBlocks[-1]`` read picks them up. If there's no text
    block, append one at the end.

    Returns the original content unchanged when ``prefix`` is empty
    (zero-allocation happy path).
    """
    if not prefix:
        return content
    if isinstance(content, str):
        return prefix + content
    if not isinstance(content, list):
        return content
    # Find the LAST text block index.
    last_text_idx = -1
    for i, block in enumerate(content):
        if isinstance(block, dict) and block.get('type') == 'text':
            last_text_idx = i
    if last_text_idx >= 0:
        block = content[last_text_idx]
        existing_text = block.get('text', '') if isinstance(block, dict) else ''
        new_block = {**block, 'text': prefix + existing_text}
        return [
            *content[:last_text_idx],
            new_block,
            *content[last_text_idx + 1:],
        ]
    # No text block — append one.
    return [*content, {'type': 'text', 'text': prefix.rstrip()}]


async def resolve_and_prepend(
    msg: Any,
    content: Any,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """Convenience: extract + resolve + prepend in one call.

    Mirrors TS ``resolveAndPrepend`` on ``inboundAttachments.ts:167-175``.
    No-op when the message has no ``file_attachments`` field (returns
    the same content reference).
    """
    attachments = extract_inbound_attachments(msg)
    if not attachments:
        return content
    prefix = await resolve_inbound_attachments(
        attachments, http_client=http_client,
    )
    return prepend_path_refs(content, prefix)


# ── Internals ─────────────────────────────────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """Strip path components + keep only filename-safe chars.

    Mirrors TS ``sanitizeFileName`` on ``inboundAttachments.ts:55-58``.
    File name comes from the network, so treat as untrusted even though
    the web composer controls it.
    """
    base = os.path.basename(name)
    cleaned = _SAFE_FILENAME_RE.sub('_', base)
    return cleaned or 'attachment'


def _uploads_dir() -> Path:
    """Per-session uploads directory under ``~/.claude/uploads/``.

    Mirrors TS ``uploadsDir`` on ``inboundAttachments.ts:60-62``. The TS
    version reads ``getClaudeConfigHomeDir()`` (which honors a
    ``CLAUDE_CONFIG_DIR`` env override); the Python port checks the
    same env var first, falling back to ``~/.claude``.
    """
    override = os.environ.get('CLAUDE_CONFIG_DIR')
    home = Path(override) if override else Path.home() / '.claude'
    return home / 'uploads' / str(get_session_id())


def _debug(message: str) -> None:
    """One-line debug logger matching the TS ``debug()`` style."""
    logger.debug('[bridge:inbound-attach] %s', message)


async def _resolve_one(
    att: dict[str, str],
    *,
    http_client: httpx.AsyncClient | None,
) -> str | None:
    """Fetch + write one attachment. Returns absolute path or None.

    Mirrors TS ``resolveOne`` on ``inboundAttachments.ts:68-117``.
    """
    token = get_bridge_access_token()
    if not token:
        _debug('skip: no oauth token')
        return None

    base_url = get_bridge_base_url()
    file_uuid = att['file_uuid']
    file_name = att['file_name']
    url = (
        f'{base_url.rstrip("/")}'
        f'/api/oauth/files/{_urlencode_segment(file_uuid)}/content'
    )

    try:
        if http_client is not None:
            response = await http_client.get(
                url,
                headers={'Authorization': f'Bearer {token}'},
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                )
    except httpx.HTTPError as err:
        _debug(f'fetch {file_uuid} threw: {err}')
        return None

    if response.status_code != 200:
        _debug(f'fetch {file_uuid} failed: status={response.status_code}')
        return None
    data = response.content

    # ``uuid_prefix-safe_name`` makes collisions impossible across
    # messages and within one message (same filename, different files).
    safe_name = _sanitize_filename(file_name)
    prefix_source = file_uuid[:8] or uuid.uuid4().hex[:8]
    prefix = _SAFE_FILENAME_RE.sub('_', prefix_source)
    out_dir = _uploads_dir()
    out_path = out_dir / f'{prefix}-{safe_name}'

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    except OSError as err:
        _debug(f'write {out_path} failed: {err}')
        return None

    _debug(f'resolved {file_uuid} -> {out_path} ({len(data)} bytes)')
    return str(out_path)


def _urlencode_segment(value: str) -> str:
    """Minimal URL-segment encoder for the file UUID path component.

    Mirrors TS ``encodeURIComponent(att.file_uuid)``. We use Python's
    ``urllib.parse.quote`` with no safe chars so any non-alphanumeric
    character is escaped.
    """
    from urllib.parse import quote

    return quote(value, safe='')


__all__ = [
    'extract_inbound_attachments',
    'prepend_path_refs',
    'resolve_and_prepend',
    'resolve_inbound_attachments',
]
