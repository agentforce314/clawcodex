"""Typed message hierarchy mirroring TypeScript src/types/message.ts and src/utils/messages.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping, TypeAlias
from uuid import uuid4

from .content_blocks import ContentBlock, content_block_to_dict, normalize_content_blocks


MessageContent: TypeAlias = str | list[ContentBlock]

MessageType: TypeAlias = Literal["user", "assistant", "system", "progress", "attachment"]

MessageOrigin: TypeAlias = Literal[
    "human",
    "tool_result",
    "compact_summary",
    "system_injection",
    "agent",
]

NO_CONTENT_MESSAGE = "[No content]"

INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"
CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "The user doesn't want to proceed with this tool use. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). "
    "To tell you how to proceed, the user said:\n"
)
NO_RESPONSE_REQUESTED = "No response requested."
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"


@dataclass
class Message:
    role: str
    content: MessageContent
    type: str = "user"
    uuid: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    isMeta: bool = False
    isVirtual: bool = False
    isCompactSummary: bool = False
    origin: MessageOrigin | None = None


@dataclass
class UserMessage(Message):
    role: str = "user"
    content: MessageContent = ""
    type: str = "user"
    toolUseResult: Any = None
    sourceToolAssistantUUID: str | None = None
    permissionMode: str | None = None
    imagePasteIds: list[int] | None = None
    summarizeMetadata: dict[str, Any] | None = None


@dataclass
class AssistantMessage(Message):
    role: str = "assistant"
    content: MessageContent = field(default_factory=list)
    type: str = "assistant"
    stop_reason: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None
    requestId: str | None = None
    apiError: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    errorDetails: str | None = None
    isApiErrorMessage: bool = False


@dataclass
class SystemMessage(Message):
    role: str = "system"
    content: MessageContent = ""
    type: str = "system"
    subtype: str | None = None
    level: str | None = None
    toolUseID: str | None = None
    preventContinuation: bool = False


@dataclass
class ProgressMessage(Message):
    role: str = "system"
    content: MessageContent = ""
    type: str = "progress"
    toolUseID: str = ""
    parentToolUseID: str = ""
    data: Any = None
    progress: str | None = None


@dataclass
class AttachmentMessage(UserMessage):
    type: str = "attachment"
    attachments: list[dict[str, Any]] = field(default_factory=list)


MessageLike: TypeAlias = Message | Mapping[str, Any]

TypedMessage: TypeAlias = (
    UserMessage
    | AssistantMessage
    | SystemMessage
    | ProgressMessage
    | AttachmentMessage
    | Message
)


def create_user_message(
    content: MessageContent,
    *,
    isMeta: bool = False,
    isVirtual: bool = False,
    isCompactSummary: bool = False,
    uuid: str | None = None,
    timestamp: str | None = None,
    toolUseResult: Any = None,
    sourceToolAssistantUUID: str | None = None,
    permissionMode: str | None = None,
    origin: MessageOrigin | None = None,
    imagePasteIds: list[int] | None = None,
    summarizeMetadata: dict[str, Any] | None = None,
) -> UserMessage:
    normalized_content = content if content else NO_CONTENT_MESSAGE
    return UserMessage(
        content=normalized_content,
        uuid=uuid or str(uuid4()),
        timestamp=timestamp or datetime.now().isoformat(),
        isMeta=isMeta,
        isVirtual=isVirtual,
        isCompactSummary=isCompactSummary,
        toolUseResult=toolUseResult,
        sourceToolAssistantUUID=sourceToolAssistantUUID,
        permissionMode=permissionMode,
        origin=origin,
        imagePasteIds=imagePasteIds,
        summarizeMetadata=summarizeMetadata,
    )


SYNTHETIC_MODEL = "<synthetic>"


def create_assistant_message(
    content: MessageContent,
    *,
    usage: dict[str, Any] | None = None,
    isVirtual: bool = False,
    stop_reason: str | None = "end_turn",
    model: str | None = None,
) -> AssistantMessage:
    if isinstance(content, str):
        from .content_blocks import TextBlock
        block_content: MessageContent = [TextBlock(text=content or NO_CONTENT_MESSAGE)]
    else:
        block_content = content
    return AssistantMessage(
        content=block_content,
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        isVirtual=isVirtual,
        stop_reason=stop_reason,
        model=model,
        usage=usage,
    )


def create_assistant_api_error_message(
    content: str,
    *,
    apiError: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    errorDetails: str | None = None,
) -> AssistantMessage:
    from .content_blocks import TextBlock
    return AssistantMessage(
        content=[TextBlock(text=content or NO_CONTENT_MESSAGE)],
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        isApiErrorMessage=True,
        apiError=apiError,
        error=error,
        errorDetails=errorDetails,
        model=SYNTHETIC_MODEL,
    )


def create_system_message(
    content: str,
    level: str = "info",
    *,
    subtype: str = "informational",
    toolUseID: str | None = None,
    preventContinuation: bool = False,
) -> SystemMessage:
    return SystemMessage(
        content=content,
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        isMeta=False,
        subtype=subtype,
        level=level,
        toolUseID=toolUseID,
        preventContinuation=preventContinuation,
    )


def create_progress_message(
    toolUseID: str,
    parentToolUseID: str,
    data: Any,
) -> ProgressMessage:
    return ProgressMessage(
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        toolUseID=toolUseID,
        parentToolUseID=parentToolUseID,
        data=data,
    )


def create_attachment_message(
    attachment: dict[str, Any],
    *,
    isMeta: bool = False,
) -> AttachmentMessage:
    return AttachmentMessage(
        content=[],
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        isMeta=isMeta,
        attachments=[attachment],
    )


def create_stop_hook_summary_message(
    hook_count: int,
    hook_infos: list[dict[str, Any]],
    hook_errors: list[str],
    prevented_continuation: bool,
    stop_reason: str,
    has_output: bool,
    suggestion_type: str,
    tool_use_id: str,
) -> SystemMessage:
    parts: list[str] = []
    parts.append(f"Ran {hook_count} stop hook(s)")
    if hook_errors:
        parts.append(f" with {len(hook_errors)} error(s)")
    if prevented_continuation:
        parts.append(f" — stopped: {stop_reason}")
    return SystemMessage(
        content="".join(parts),
        uuid=str(uuid4()),
        timestamp=datetime.now().isoformat(),
        type="system",
        subtype="stop_hook_summary",
        level="info",
        toolUseID=tool_use_id,
        preventContinuation=prevented_continuation,
    )


def create_user_interruption_message(
    *,
    tool_use: bool = False,
) -> UserMessage:
    from . import content_blocks as cb
    content_str = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return create_user_message(
        content=content_str,
        isMeta=True,
    )


def create_message(
    role: str,
    content: MessageContent,
    *,
    timestamp: str | None = None,
    isMeta: bool = False,
) -> Message:
    ts = timestamp or datetime.now().isoformat()
    if role == "user":
        return create_user_message(content, isMeta=isMeta, timestamp=ts)
    if role == "assistant":
        return create_assistant_message(content)
    if role == "system":
        return SystemMessage(content=content, timestamp=ts, isMeta=isMeta)
    return Message(role=role, content=content, timestamp=ts, isMeta=isMeta)


def normalize_messages_for_api(messages: list[MessageLike]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        result = normalize_message_for_api(message)
        if result is not None:
            if normalized and normalized[-1]["role"] == result["role"] == "user":
                if _should_merge_user_messages(normalized[-1], result):
                    normalized[-1] = _merge_user_api_messages(normalized[-1], result)
                else:
                    normalized.append(result)
            else:
                normalized.append(result)
    normalized = ensure_tool_result_pairing(normalized)
    return normalized


def normalize_message_for_api(message: MessageLike) -> dict[str, Any] | None:
    msg_type = _get_field(message, "type", "user")
    if msg_type == "progress":
        return None
    if msg_type == "system" and not _is_system_local_command(message):
        return None
    if _get_field(message, "isVirtual", False):
        return None

    role = _get_field(message, "role", "user")
    api_role = role if role in {"user", "assistant"} else "user"
    content = _get_field(message, "content", "")

    if isinstance(content, str):
        normalized_content: str | list[dict[str, Any]] = content
    elif isinstance(content, list):
        normalized_content = [content_block_to_dict(block) for block in content]
    else:
        normalized_content = str(content)

    return {"role": api_role, "content": normalized_content}


def ensure_tool_result_pairing(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and repair tool_use/tool_result pairing.

    Mirrors TS ensureToolResultPairing (messages.ts).
    - Forward: inserts synthetic error tool_result blocks for tool_use blocks missing results
    - Reverse: strips orphaned tool_result blocks referencing non-existent tool_use blocks
    - Deduplicates tool_use IDs across the full message array
    """
    result: list[dict[str, Any]] = []
    all_seen_tool_use_ids: set[str] = set()

    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.get("role") != "assistant":
            content = msg.get("content", "")
            if (
                msg.get("role") == "user"
                and isinstance(content, list)
                and (not result or result[-1].get("role") != "assistant")
            ):
                stripped = [
                    block for block in content
                    if not (isinstance(block, dict) and block.get("type") == "tool_result")
                ]
                if len(stripped) != len(content):
                    if stripped:
                        result.append({"role": "user", "content": stripped})
                    elif not result:
                        result.append({"role": "user", "content": "[Orphaned tool result removed]"})
                    i += 1
                    continue
            result.append(msg)
            i += 1
            continue

        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            i += 1
            continue

        seen_tool_use_ids: set[str] = set()
        final_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                block_id = block.get("id", "")
                if block_id in all_seen_tool_use_ids:
                    continue
                all_seen_tool_use_ids.add(block_id)
                seen_tool_use_ids.add(block_id)
            final_content.append(block)

        if not final_content:
            final_content = [{"type": "text", "text": "[Tool use interrupted]"}]

        assistant_msg = {"role": "assistant", "content": final_content}
        result.append(assistant_msg)

        tool_use_ids = list(seen_tool_use_ids)
        if not tool_use_ids:
            i += 1
            continue

        existing_tool_result_ids: set[str] = set()
        next_msg = messages[i + 1] if i + 1 < len(messages) else None

        if next_msg and next_msg.get("role") == "user":
            nc = next_msg.get("content", "")
            if isinstance(nc, list):
                for block in nc:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        existing_tool_result_ids.add(block.get("tool_use_id", ""))

        missing_ids = [tid for tid in tool_use_ids if tid not in existing_tool_result_ids]
        orphaned_ids = {tid for tid in existing_tool_result_ids if tid not in set(tool_use_ids)}

        if not missing_ids and not orphaned_ids:
            i += 1
            continue

        synthetic_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tid,
                "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                "is_error": True,
            }
            for tid in missing_ids
        ]

        if next_msg and next_msg.get("role") == "user":
            nc = next_msg.get("content", "")
            if isinstance(nc, list):
                filtered = [
                    block for block in nc
                    if not (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id", "") in orphaned_ids
                    )
                ]
            else:
                filtered = [{"type": "text", "text": nc}] if nc else []

            patched_content = synthetic_blocks + filtered
            if patched_content:
                result.append({"role": "user", "content": patched_content})
            else:
                result.append({"role": "user", "content": NO_CONTENT_MESSAGE})
            i += 2
        else:
            if synthetic_blocks:
                result.append({"role": "user", "content": synthetic_blocks})
            i += 1

    return result


def _has_tool_result_blocks(msg: dict[str, Any]) -> bool:
    content = msg.get("content", "")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _has_non_tool_result_blocks(msg: dict[str, Any]) -> bool:
    content = msg.get("content", "")
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return any(
        not (isinstance(block, dict) and block.get("type") == "tool_result")
        for block in content
    )


def _should_merge_user_messages(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> bool:
    if _has_tool_result_blocks(existing) and _has_non_tool_result_blocks(new):
        return False
    if _has_tool_result_blocks(new) and _has_non_tool_result_blocks(existing):
        return False
    return True


def _hoist_tool_results(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
    other_blocks = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]
    return tool_results + other_blocks


def _merge_user_api_messages(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    ec = existing["content"]
    nc = new["content"]
    if isinstance(ec, str):
        ec = [{"type": "text", "text": ec}]
    if isinstance(nc, str):
        nc = [{"type": "text", "text": nc}]
    return {"role": "user", "content": _hoist_tool_results(ec + nc)}


def _is_system_local_command(message: MessageLike) -> bool:
    return _get_field(message, "subtype", None) == "local_command"


def message_to_dict(message: MessageLike) -> dict[str, Any]:
    role = _get_field(message, "role", "user")
    content = _get_field(message, "content", "")
    timestamp = _get_field(message, "timestamp", datetime.now().isoformat())

    if isinstance(content, str):
        serialized_content: str | list[dict[str, Any]] = content
    elif isinstance(content, list):
        serialized_content = [content_block_to_dict(block) for block in content]
    else:
        serialized_content = str(content)

    payload: dict[str, Any] = {
        "role": role,
        "content": serialized_content,
        "type": _get_field(message, "type", role),
        "uuid": _get_field(message, "uuid", ""),
        "timestamp": timestamp,
        "isMeta": _get_field(message, "isMeta", False),
        "isVirtual": _get_field(message, "isVirtual", False),
        "isCompactSummary": _get_field(message, "isCompactSummary", False),
    }

    for attr in (
        "stop_reason", "subtype", "level", "progress",
        "toolUseResult", "sourceToolAssistantUUID", "permissionMode",
        "isApiErrorMessage", "apiError", "error", "errorDetails",
        "model", "origin", "toolUseID", "parentToolUseID", "data",
        "imagePasteIds", "summarizeMetadata", "preventContinuation",
    ):
        val = _get_field(message, attr, None)
        if val is not None and val is not False:
            payload[attr] = val

    attachments = _get_field(message, "attachments", None)
    if attachments:
        payload["attachments"] = list(attachments)

    return payload


def message_from_dict(data: Mapping[str, Any]) -> Message:
    role = str(data.get("role", "user"))
    msg_type = str(data.get("type", role))
    content = _normalize_loaded_content(data.get("content", ""))
    timestamp = str(data.get("timestamp", datetime.now().isoformat()))
    uuid = str(data.get("uuid", str(uuid4())))
    is_meta = bool(data.get("isMeta", data.get("_is_internal", False)))
    is_virtual = bool(data.get("isVirtual", False))
    is_compact_summary = bool(data.get("isCompactSummary", False))
    origin = data.get("origin")

    if msg_type == "assistant" or role == "assistant":
        return AssistantMessage(
            content=content,
            uuid=uuid,
            timestamp=timestamp,
            isMeta=is_meta,
            isVirtual=is_virtual,
            isCompactSummary=is_compact_summary,
            stop_reason=data.get("stop_reason") if isinstance(data.get("stop_reason"), str) else None,
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
            requestId=data.get("requestId") if isinstance(data.get("requestId"), str) else None,
            isApiErrorMessage=bool(data.get("isApiErrorMessage", False)),
            apiError=data.get("apiError") if isinstance(data.get("apiError"), dict) else None,
            error=data.get("error") if isinstance(data.get("error"), dict) else None,
            errorDetails=data.get("errorDetails") if isinstance(data.get("errorDetails"), str) else None,
            origin=origin,
        )

    if msg_type == "progress":
        return ProgressMessage(
            content=content,
            uuid=uuid,
            timestamp=timestamp,
            isMeta=is_meta,
            toolUseID=str(data.get("toolUseID", "")),
            parentToolUseID=str(data.get("parentToolUseID", "")),
            data=data.get("data"),
            progress=data.get("progress") if isinstance(data.get("progress"), str) else None,
            origin=origin,
        )

    if msg_type == "system" or role == "system":
        subtype = data.get("subtype")
        level = data.get("level")
        return SystemMessage(
            content=content,
            uuid=uuid,
            timestamp=timestamp,
            isMeta=is_meta,
            isVirtual=is_virtual,
            isCompactSummary=is_compact_summary,
            subtype=subtype if isinstance(subtype, str) else None,
            level=level if isinstance(level, str) else None,
            toolUseID=data.get("toolUseID") if isinstance(data.get("toolUseID"), str) else None,
            preventContinuation=bool(data.get("preventContinuation", False)),
            origin=origin,
        )

    if msg_type == "attachment":
        raw_attachments = data.get("attachments")
        attachments = [dict(a) for a in raw_attachments] if isinstance(raw_attachments, list) else []
        return AttachmentMessage(
            content=content,
            uuid=uuid,
            timestamp=timestamp,
            isMeta=is_meta,
            isVirtual=is_virtual,
            isCompactSummary=is_compact_summary,
            attachments=attachments,
            origin=origin,
        )

    if role == "user":
        raw_attachments = data.get("attachments")
        attachments = [dict(a) for a in raw_attachments] if isinstance(raw_attachments, list) else []
        if attachments:
            return AttachmentMessage(
                content=content,
                uuid=uuid,
                timestamp=timestamp,
                isMeta=is_meta,
                isVirtual=is_virtual,
                isCompactSummary=is_compact_summary,
                attachments=attachments,
                origin=origin,
            )
        return UserMessage(
            content=content,
            uuid=uuid,
            timestamp=timestamp,
            isMeta=is_meta,
            isVirtual=is_virtual,
            isCompactSummary=is_compact_summary,
            toolUseResult=data.get("toolUseResult"),
            sourceToolAssistantUUID=data.get("sourceToolAssistantUUID"),
            permissionMode=data.get("permissionMode"),
            imagePasteIds=data.get("imagePasteIds"),
            summarizeMetadata=data.get("summarizeMetadata"),
            origin=origin,
        )

    return Message(
        role=role,
        content=content,
        uuid=uuid,
        timestamp=timestamp,
        isMeta=is_meta,
        isVirtual=is_virtual,
        isCompactSummary=is_compact_summary,
        origin=origin,
    )


def is_tool_use_request_message(message: Message) -> bool:
    if not isinstance(message, AssistantMessage):
        return False
    content = message.content
    if isinstance(content, list):
        return any(
            getattr(block, "type", None) == "tool_use" or
            (isinstance(block, dict) and block.get("type") == "tool_use")
            for block in content
        )
    return False


def is_tool_use_result_message(message: Message) -> bool:
    if message.role != "user":
        return False
    content = message.content
    if isinstance(content, list):
        return any(
            getattr(block, "type", None) == "tool_result" or
            (isinstance(block, dict) and block.get("type") == "tool_result")
            for block in content
        )
    return False


def get_tool_use_ids(message: Message) -> list[str]:
    ids: list[str] = []
    if isinstance(message, AssistantMessage) and isinstance(message.content, list):
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                ids.append(getattr(block, "id", ""))
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                ids.append(str(block.get("id", "")))
    return ids


def get_last_assistant_message(messages: list[Message]) -> AssistantMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            return msg
    return None


def _normalize_loaded_content(value: Any) -> MessageContent:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return normalize_content_blocks(value)
    return str(value)


def _get_field(message: MessageLike, name: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(name, default)
    return getattr(message, name, default)


__all__ = [
    "CANCEL_MESSAGE",
    "INTERRUPT_MESSAGE",
    "INTERRUPT_MESSAGE_FOR_TOOL_USE",
    "NO_CONTENT_MESSAGE",
    "NO_RESPONSE_REQUESTED",
    "REJECT_MESSAGE",
    "REJECT_MESSAGE_WITH_REASON_PREFIX",
    "SYNTHETIC_MODEL",
    "SYNTHETIC_TOOL_RESULT_PLACEHOLDER",
    "AssistantMessage",
    "AttachmentMessage",
    "Message",
    "MessageContent",
    "MessageLike",
    "MessageOrigin",
    "MessageType",
    "ProgressMessage",
    "SystemMessage",
    "TypedMessage",
    "UserMessage",
    "create_assistant_api_error_message",
    "create_assistant_message",
    "create_attachment_message",
    "create_message",
    "create_progress_message",
    "create_stop_hook_summary_message",
    "create_system_message",
    "create_user_interruption_message",
    "create_user_message",
    "get_last_assistant_message",
    "get_tool_use_ids",
    "is_tool_use_request_message",
    "is_tool_use_result_message",
    "message_from_dict",
    "message_to_dict",
    "normalize_message_for_api",
    "normalize_messages_for_api",
    "ensure_tool_result_pairing",
]
