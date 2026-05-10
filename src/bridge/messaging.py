"""CCR bridge ingress router + message filters/adapters.

Ports the **router** half (``handle_ingress_message``, type guards,
``normalize_control_message_keys``) and the **filter/adapter** half
(``is_eligible_bridge_message``, ``extract_title_text``,
``make_result_message``, ``RemotePermissionResponse``) of
``typescript/src/bridge/bridgeMessaging.ts``.

The **server-control-request handler** (``handle_server_control_request``
plus the per-subtype handler dispatch) lives in
``src/bridge/messaging_handlers.py`` so the router stays small and stable
while the handler set evolves.

Concurrency: ingress messages flow on a single asyncio task; the
``BoundedUUIDSet`` instances passed in must not be shared across tasks
without external locking.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Union

from .bounded_uuid_set import BoundedUUIDSet
from .sdk_types import (
    SDKControlRequest,
    SDKControlResponse,
    SDKMessage,
    SDKResultSuccess,
)

logger = logging.getLogger(__name__)

# ─── Type guards ───────────────────────────────────────────────────────────


def is_sdk_message(value: object) -> bool:
    """True if ``value`` looks like an SDKMessage (has a string ``type``).

    Mirrors ``bridgeMessaging.ts:36-43``. Permissive on purpose — callers
    narrow further by branching on ``type``.
    """
    return (
        isinstance(value, dict)
        and 'type' in value
        and isinstance(value.get('type'), str)
    )


def is_sdk_control_response(value: object) -> bool:
    """True for ``{type:'control_response', response}``-shaped dicts."""
    return (
        isinstance(value, dict)
        and value.get('type') == 'control_response'
        and 'response' in value
    )


def is_sdk_control_request(value: object) -> bool:
    """True for ``{type:'control_request', request_id, request}``-shaped dicts."""
    return (
        isinstance(value, dict)
        and value.get('type') == 'control_request'
        and 'request_id' in value
        and 'request' in value
    )


# ─── camelCase ↔ snake_case normalization for control envelopes ────────────

# Hand-ported known-key set from ``typescript/src/utils/controlMessageCompat.ts``
# (referenced by ``bridgeMessaging.ts:141``). Unknown keys pass through
# unchanged with a debug log entry.
_KNOWN_CAMEL_TO_SNAKE: dict[str, str] = {
    'requestId': 'request_id',
    'toolUseId': 'tool_use_id',
    'parentToolUseId': 'parent_tool_use_id',
    'controlRequest': 'control_request',
    'controlResponse': 'control_response',
    'controlCancelRequest': 'control_cancel_request',
    'sessionId': 'session_id',
    'workerEpoch': 'worker_epoch',
    'workerJwt': 'worker_jwt',
    'apiBaseUrl': 'api_base_url',
    'expiresIn': 'expires_in',
    'updatedInput': 'updated_input',
    'maxThinkingTokens': 'max_thinking_tokens',
    'permissionMode': 'permission_mode',
    'toolName': 'tool_name',
}


def normalize_control_message_keys(value: object) -> object:
    """Walk the wire payload and normalize known camelCase keys to snake_case.

    Recurses into nested dicts and lists. Unknown camelCase keys are
    passed through unchanged with a one-line debug log so the messaging
    layer surfaces wire-format drift without silently dropping anything.

    Mirrors ``typescript/src/utils/controlMessageCompat.ts``.
    """
    if isinstance(value, list):
        return [normalize_control_message_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, val in value.items():
        normalized_key = _KNOWN_CAMEL_TO_SNAKE.get(key, key)
        if normalized_key == key and _looks_camel_case(key):
            logger.debug(
                '[bridge:messaging] Unknown camelCase key passed through unchanged: %s',
                key,
            )
        out[normalized_key] = normalize_control_message_keys(val)
    return out


def _looks_camel_case(key: str) -> bool:
    """Heuristic: contains an internal uppercase letter and starts lowercase."""
    return bool(key) and key[0].islower() and any(c.isupper() for c in key[1:])


# ─── Ingress router ────────────────────────────────────────────────────────


def handle_ingress_message(
    data: str,
    recent_posted_uuids: BoundedUUIDSet,
    recent_inbound_uuids: BoundedUUIDSet,
    on_inbound_message: Callable[[dict[str, Any]], Union[None, Awaitable[None]]] | None = None,
    on_permission_response: Callable[[dict[str, Any]], None] | None = None,
    on_control_request: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Parse an ingress WebSocket message and route it to the right handler.

    Mirrors ``bridgeMessaging.ts:132-208``.

    Routing rules:
      1. Parse JSON (silently drop on parse error — log only).
      2. Normalize control-message keys.
      3. ``control_response`` → ``on_permission_response``.
      4. ``control_request`` → ``on_control_request``.
      5. UUID dedup: drop if in ``recent_posted_uuids`` (echo of our own
         write) or ``recent_inbound_uuids`` (re-delivery from history
         replay).
      6. ``user`` SDKMessage → ``on_inbound_message`` (and add UUID to
         ``recent_inbound_uuids``).
      7. Other SDK message types → log + drop (server only wants user
         turns on the read side; we filter in
         ``is_eligible_bridge_message`` for the write side).
    """
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        logger.debug('[bridge:messaging] Failed to parse ingress message: %s', exc)
        return

    parsed = normalize_control_message_keys(raw)

    if is_sdk_control_response(parsed):
        logger.debug('[bridge:messaging] Ingress message type=control_response')
        if on_permission_response is not None:
            on_permission_response(parsed)  # type: ignore[arg-type]
        return

    if is_sdk_control_request(parsed):
        subtype = parsed.get('request', {}).get('subtype', '<unknown>')  # type: ignore[union-attr]
        logger.debug('[bridge:messaging] Inbound control_request subtype=%s', subtype)
        if on_control_request is not None:
            on_control_request(parsed)  # type: ignore[arg-type]
        return

    if not is_sdk_message(parsed):
        return

    msg = parsed  # narrowed to dict by is_sdk_message
    msg_uuid = msg.get('uuid') if isinstance(msg.get('uuid'), str) else None  # type: ignore[union-attr]

    if msg_uuid is not None and recent_posted_uuids.has(msg_uuid):
        logger.debug(
            '[bridge:messaging] Ignoring echo: type=%s uuid=%s',
            msg.get('type'),  # type: ignore[union-attr]
            msg_uuid,
        )
        return

    if msg_uuid is not None and recent_inbound_uuids.has(msg_uuid):
        logger.debug(
            '[bridge:messaging] Ignoring re-delivered inbound: type=%s uuid=%s',
            msg.get('type'),  # type: ignore[union-attr]
            msg_uuid,
        )
        return

    msg_type = msg.get('type')  # type: ignore[union-attr]
    logger.debug(
        '[bridge:messaging] Ingress message type=%s%s',
        msg_type,
        f' uuid={msg_uuid}' if msg_uuid else '',
    )

    if msg_type == 'user':
        if msg_uuid is not None:
            recent_inbound_uuids.add(msg_uuid)
        if on_inbound_message is not None:
            result = on_inbound_message(msg)  # type: ignore[arg-type]
            # If the handler is async, fire-and-forget on the running loop.
            # Matches TS ``void onInboundMessage?.(parsed)`` discard.
            # Caller MUST invoke ``handle_ingress_message`` from inside a
            # running asyncio loop when ``on_inbound_message`` is async;
            # use ``get_running_loop`` (raises RuntimeError if no loop)
            # rather than the deprecated ``get_event_loop``.
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(result)  # type: ignore[arg-type]
    else:
        logger.debug(
            '[bridge:messaging] Ignoring non-user inbound message: type=%s',
            msg_type,
        )


# ─── Forward-filter for outbound bridge messages ───────────────────────────


def is_eligible_bridge_message(message: dict[str, Any]) -> bool:
    """True if ``message`` should be forwarded to the bridge transport.

    Mirrors ``bridgeMessaging.ts:77-88``: filters out virtual REPL
    inner-call messages, tool_results, progress, non-human origins, etc.
    Forwards user/assistant turns and ``system`` messages of subtype
    ``local_command``.
    """
    msg_type = message.get('type')
    if msg_type in ('user', 'assistant'):
        if message.get('isVirtual'):
            return False
        return True
    if msg_type == 'system':
        return message.get('subtype') == 'local_command'
    return False


# Mirror of TS ``utils/displayTags.ts:14`` ``XML_TAG_BLOCK_PATTERN`` —
# lowercase-only opening (so user prose like "<Button>" passes through),
# backreference for the closing tag (so adjacent blocks don't merge and
# mismatched ``<foo>x</bar>`` is left alone), optional attributes,
# multi-line content (``[\s\S]`` is the JS equivalent of ``re.DOTALL``).
# Trailing ``\n?`` strips the newline that wraps system-injected blocks.
_DISPLAY_TAG_RE = re.compile(r'<([a-z][\w-]*)(?:\s[^>]*)?>[\s\S]*?</\1>\n?')


def _strip_display_tags(text: str) -> str:
    """Strip XML-like display tags from ``text``.

    Equivalent to TS ``stripDisplayTagsAllowEmpty`` (``displayTags.ts:37``)
    — returns empty string when all content is tags. Used by
    ``extract_title_text`` to skip pure-XML messages during bridge title
    derivation.
    """
    return _DISPLAY_TAG_RE.sub('', text).strip()


def extract_title_text(message: dict[str, Any]) -> str | None:
    """Extract title-worthy text from a Message for ``onUserMessage``.

    Returns None for messages that shouldn't title the session: non-user,
    meta (nudges), tool results, compact summaries, non-human origins
    (task notifications, channel messages), or pure display-tag content.

    Mirrors ``bridgeMessaging.ts:103-122``.
    """
    if message.get('type') != 'user':
        return None
    # Match JS truthy semantics: ``isMeta``/``isCompactSummary`` are
    # bool flags (False/None means "not meta"), but ``toolUseResult`` is
    # a dict where even ``{}`` should disqualify (in JS ``{}`` is truthy;
    # in Python ``{}`` is falsy, so we check for *presence*, not truthiness).
    if message.get('isMeta'):
        return None
    if 'toolUseResult' in message and message.get('toolUseResult') is not None:
        return None
    if message.get('isCompactSummary'):
        return None
    origin = message.get('origin')
    if isinstance(origin, dict) and origin.get('kind') != 'human':
        return None

    inner = message.get('message') or {}
    content = inner.get('content') if isinstance(inner, dict) else None
    raw: str | None
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        raw = None
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                raw = block.get('text')
                if isinstance(raw, str):
                    break
                raw = None
    else:
        raw = None

    if not raw:
        return None
    cleaned = _strip_display_tags(raw)
    return cleaned or None


# ─── Result message (for session archival on teardown) ─────────────────────


def make_result_message(session_id: str) -> SDKResultSuccess:
    """Build a minimal ``SDKResultSuccess`` for session archival.

    The server needs this event before a WS close to trigger archival.
    Mirrors ``bridgeMessaging.ts:399-416``.
    """
    return {
        'type': 'result',
        'subtype': 'success',
        'duration_ms': 0,
        'duration_api_ms': 0,
        'is_error': False,
        'num_turns': 0,
        'result': '',
        'stop_reason': None,
        'total_cost_usd': 0.0,
        'usage': {},
        'modelUsage': {},
        'permission_denials': [],
        'session_id': session_id,
        'uuid': str(_uuid.uuid4()),
    }


# ─── Remote permission response (Phase 4 + Direct Connect consumer) ────────


@dataclass(frozen=True)
class AllowResponse:
    """Permission allow with possibly-rewritten input."""

    updated_input: dict[str, Any] = field(default_factory=dict)
    behavior: str = 'allow'


@dataclass(frozen=True)
class DenyResponse:
    """Permission deny with a user-visible message."""

    message: str
    behavior: str = 'deny'


# Discriminated union — callers branch on ``isinstance(...)``.
RemotePermissionResponse = Union[AllowResponse, DenyResponse]


def remote_permission_response_from_dict(payload: dict[str, Any]) -> RemotePermissionResponse:
    """Wire-format → ``RemotePermissionResponse``.

    Mirrors ``RemotePermissionResponse`` discriminated union from
    ``remote/RemoteSessionManager.ts:40-48``.
    """
    behavior = payload.get('behavior')
    if behavior == 'allow':
        updated = payload.get('updated_input') or payload.get('updatedInput') or {}
        if not isinstance(updated, dict):
            raise ValueError(
                f'allow response: updated_input must be dict, got {type(updated).__name__}'
            )
        return AllowResponse(updated_input=updated)
    if behavior == 'deny':
        msg = payload.get('message')
        if not isinstance(msg, str):
            raise ValueError('deny response: message must be a string')
        return DenyResponse(message=msg)
    raise ValueError(f'unknown permission behavior: {behavior!r}')


__all__ = [
    'AllowResponse',
    'DenyResponse',
    'RemotePermissionResponse',
    'extract_title_text',
    'handle_ingress_message',
    'is_eligible_bridge_message',
    'is_sdk_control_request',
    'is_sdk_control_response',
    'is_sdk_message',
    'make_result_message',
    'normalize_control_message_keys',
    'remote_permission_response_from_dict',
]
