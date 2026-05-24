"""Convert internal Message types to wire-format SDKMessage dicts.

Ports the **outbound half** of ``typescript/src/utils/messages/mappers.ts``.

The TS file lives at ``utils/messages/mappers.ts``; the Python port uses
a flat filename (``message_mappers.py``) because ``src/utils/messages.py``
already exists as a single file (a name-collision with a subfolder would
require relocating the existing module). Functional surface is identical.

**Scope**: only ``to_sdk_messages`` is ported in Phase 2 — it's the one
used by Phase 5 (``remoteBridgeCore``) and Phase 6 (``replBridge``) flush
paths. The inbound mapper (``toInternalMessages``), compact-metadata
helpers, ExitPlanMode v2 plan injection, and local-command-output
synthesis are deferred:

* ``toInternalMessages`` — not needed by the bridge orchestrators (the
  v2 bridge reads inbound SDK messages via the existing
  ``messaging.handle_ingress_message`` dispatcher).
* Local-command-output synthesis — needs ``strip_ansi`` + LOCAL_COMMAND_*
  XML constants + ``create_assistant_message``. Routed through the
  fallthrough no-op for now; can be backfilled when a v2 bridge user
  reports missing /cost or /voice output in the web UI.
* ExitPlanMode v2 input normalization — needs ``utils/plans.get_plan()``;
  same fallthrough no-op deferral.

Both deferrals are safe: the affected message types are rare in bridge
flushes, and skipping them just means slightly less context flows to the
web client (matching what the v1 bridge did before TS added the
synthesis paths).
"""

from __future__ import annotations

from typing import Any

from src.bootstrap.state import get_session_id
from src.types.content_blocks import content_block_to_dict
from src.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)


def to_sdk_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Map local Message[] to wire-format SDKMessage[] dicts.

    Mirrors TS ``toSDKMessages`` on ``utils/messages/mappers.ts:115-181``.

    Returns a list of plain dicts (not TypedDicts) because the bridge
    transport serializes via ``json.dumps`` and TypedDicts have no
    runtime difference from regular dicts. Each dict carries:

    * ``type``: ``"user"`` / ``"assistant"`` / ``"system"`` (compact_boundary)
    * ``session_id``: from ``bootstrap.state.get_session_id()``
    * ``parent_tool_use_id``: ``None`` (sub-agent context not used by bridge)
    * ``uuid``: original message UUID (preserved for dedup on receipt)
    * ``message``: the inner Anthropic-format payload (for user/assistant)
    * ``timestamp`` / ``isSynthetic`` / ``tool_use_result``: user-only
    * ``compact_metadata``: system compact_boundary only
    * ``error``: assistant-only

    Message types that don't map (``progress``, ``attachment``,
    pure-input system, local_command without output, etc.) are silently
    dropped — matches the TS ``flatMap`` returning ``[]``.
    """
    out: list[dict[str, Any]] = []
    session_id = get_session_id()
    for message in messages:
        mapped = _map_one(message, session_id)
        if mapped is not None:
            out.append(mapped)
    return out


def _map_one(message: Message, session_id: str) -> dict[str, Any] | None:
    """Map a single message to its SDK wire dict (or None to drop)."""
    if isinstance(message, AssistantMessage):
        return _assistant_to_sdk(message, session_id)
    if isinstance(message, UserMessage):
        return _user_to_sdk(message, session_id)
    if isinstance(message, SystemMessage):
        return _system_to_sdk(message, session_id)
    # Other Message subtypes (progress, attachment, bare Message) are
    # dropped — matches TS default branch returning [].
    return None


def _assistant_to_sdk(
    message: AssistantMessage, session_id: str
) -> dict[str, Any]:
    """Mirror TS lines 118-128.

    The inner ``message`` payload populates ``id``/``model``/``stop_reason``/
    ``usage`` (when present on the source) — TS passes through the full
    ``APIAssistantMessage`` and downstream consumers (Android
    ``SdkAssistantMessage``, mobile-apps deserializers) expect these fields
    to exist. See TS comment at ``mappers.ts:205-207``.
    """
    inner = _build_assistant_inner(message)
    out: dict[str, Any] = {
        'type': 'assistant',
        'message': inner,
        'session_id': session_id,
        'parent_tool_use_id': None,
        'uuid': message.uuid,
    }
    if message.error is not None:
        out['error'] = message.error
    return out


def _user_to_sdk(message: UserMessage, session_id: str) -> dict[str, Any]:
    """Mirror TS lines 130-147."""
    inner = _build_user_inner(message)
    out: dict[str, Any] = {
        'type': 'user',
        'message': inner,
        'session_id': session_id,
        'parent_tool_use_id': None,
        'uuid': message.uuid,
        'timestamp': message.timestamp,
        'isSynthetic': _is_user_synthetic(message),
    }
    if message.toolUseResult is not None:
        # Structured tool output — see TS comment lines 139-145 explaining
        # why this rides on the protobuf catchall so web viewers can see
        # things like BriefTool's file_uuid without polluting model context.
        out['tool_use_result'] = message.toolUseResult
    return out


def _is_user_synthetic(message: UserMessage) -> bool:
    """Per TS ``isSynthetic`` for user messages (``mappers.ts:138``):

    ``message.isMeta || message.isVisibleInTranscriptOnly``

    Python ``UserMessage`` has ``isMeta`` and ``isVirtual``; ``isVirtual``
    is the closest analogue to TS ``isVisibleInTranscriptOnly`` (both
    mean "not sent to the model, transcript-only"). Documented here so a
    future porter doesn't reverse-engineer the mapping incorrectly.

    Note: this is intentionally distinct from
    ``src.utils.messages.is_synthetic`` — that helper covers assistant
    messages too (model == SYNTHETIC_MODEL) and uses a different rule
    for that case. The bridge wire format only needs the user-message
    rule, so we keep it local.
    """
    return bool(message.isMeta or message.isVirtual)


def _system_to_sdk(
    message: SystemMessage, session_id: str
) -> dict[str, Any] | None:
    """Mirror TS lines 148-176.

    Compact-boundary messages with metadata are emitted; local-command
    messages are deferred (see module docstring).
    """
    if message.subtype == 'compact_boundary':
        # Bare ``SystemMessage`` doesn't carry compactMetadata in the
        # current Python type. Phase 5+ will add the field if needed —
        # for now emit the system event without the metadata so the web
        # UI at least knows a compaction happened.
        meta = getattr(message, 'compactMetadata', None)
        out: dict[str, Any] = {
            'type': 'system',
            'subtype': 'compact_boundary',
            'session_id': session_id,
            'uuid': message.uuid,
        }
        if meta is not None:
            out['compact_metadata'] = _to_sdk_compact_metadata(meta)
        return out
    # local_command path deferred (see module docstring) — return None
    # to drop, matching the TS ``return []`` fallthrough.
    return None


def _serialize_content(content: Any) -> Any:
    """Convert a Message ``content`` field to its JSON-serializable form.

    Lists get per-block conversion via ``content_block_to_dict``; strings
    and other primitives pass through unchanged. Used by both the user
    and assistant inner-message builders.
    """
    if isinstance(content, list):
        return [
            content_block_to_dict(b) if not isinstance(b, dict) else b
            for b in content
        ]
    return content


def _build_user_inner(message: UserMessage) -> dict[str, Any]:
    """Build the inner ``message`` object for a user wire payload.

    TS preserves ``message.message`` (the original API-shaped object).
    Python doesn't carry that as a separate field; we reconstruct from
    ``role`` + ``content`` so the wire shape matches Anthropic's
    ``MessageParam``.
    """
    return {'role': message.role, 'content': _serialize_content(message.content)}


def _build_assistant_inner(message: AssistantMessage) -> dict[str, Any]:
    """Build the inner ``message`` object for an assistant wire payload.

    Mirrors what TS ``SDKAssistantMessage.message`` carries: the full
    ``APIAssistantMessage`` shape with ``id``, ``type``, ``role``,
    ``model``, ``stop_reason``, ``usage``, ``content``. Downstream
    consumers (Android ``SdkAssistantMessage`` deserializer, mobile-apps
    parsers) require these fields — without them the message is rejected
    or rendered malformed.

    Python ``AssistantMessage`` doesn't carry an Anthropic-issued ``id``,
    so we synthesize one from the message UUID (``msg_{uuid}``) which is
    stable across re-emits and matches the wire-format prefix Anthropic
    uses. ``type`` is always ``'message'`` (the only valid value).
    Optional fields (``model``, ``stop_reason``, ``usage``) are emitted
    when present on the source and elided when ``None`` to keep the
    payload compact.
    """
    inner: dict[str, Any] = {
        'id': f'msg_{message.uuid}',
        'type': 'message',
        'role': message.role,
        'content': _serialize_content(message.content),
    }
    if message.model is not None:
        inner['model'] = message.model
    if message.stop_reason is not None:
        inner['stop_reason'] = message.stop_reason
    if message.usage is not None:
        inner['usage'] = message.usage
    return inner


def _to_sdk_compact_metadata(meta: Any) -> dict[str, Any]:
    """Mirror TS ``toSDKCompactMetadata`` on ``mappers.ts:78-93``.

    Accepts either a dataclass-style object (``meta.trigger``,
    ``meta.preTokens``, optional ``meta.preservedSegment``) or a dict.
    Converts the inner ``preservedSegment`` camelCase keys to snake_case
    wire format (``head_uuid``/``anchor_uuid``/``tail_uuid``).
    """
    def _attr(obj: Any, name: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    out: dict[str, Any] = {
        'trigger': _attr(meta, 'trigger'),
        'pre_tokens': _attr(meta, 'preTokens'),
    }
    seg = _attr(meta, 'preservedSegment')
    if seg is not None:
        out['preserved_segment'] = {
            'head_uuid': _attr(seg, 'headUuid'),
            'anchor_uuid': _attr(seg, 'anchorUuid'),
            'tail_uuid': _attr(seg, 'tailUuid'),
        }
    return out


__all__ = ['to_sdk_messages']
