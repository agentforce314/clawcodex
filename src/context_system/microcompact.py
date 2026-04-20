"""
Microcompact: lightweight message pre-processing (Layer 3).

Strips images/documents from messages and clears old tool results
when the cache is cold (time-based trigger). This reduces tokens
sent to the API without losing the model-visible history structure.

Port of ``typescript/src/services/compact/microCompact.ts``.

Supports both typed ``Message`` objects (WS-1) and raw ``dict`` messages
for backward compatibility.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Union

from ..token_estimation import count_tokens, rough_token_count
from ..types.content_blocks import (
    ContentBlock,
    ImageBlock,
    DocumentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ..types.messages import Message, UserMessage, AssistantMessage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Token size estimate for images/documents in tool results
IMAGE_TOKEN_SIZE = 2000

# Marker inserted for content-cleared tool results
CLEARED_MESSAGE = "[Old tool result content cleared]"

# Tools eligible for microcompact (same as TypeScript COMPACTABLE_TOOLS)
COMPACTABLE_TOOL_NAMES: frozenset[str] = frozenset([
    "Read",
    "Bash",
    "Shell",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Edit",
    "Write",
])

# ---------------------------------------------------------------------------
# Time-based microcompact configuration
# (port of typescript/src/services/compact/timeBasedMCConfig.ts)
# ---------------------------------------------------------------------------

DEFAULT_TIME_BASED_MC_ENABLED = True
DEFAULT_GAP_THRESHOLD_MINUTES = 60
DEFAULT_KEEP_RECENT = 5


class TimeBasedMCConfig:
    """Configuration for time-based microcompact."""
    __slots__ = ("enabled", "gap_threshold_minutes", "keep_recent")

    def __init__(
        self,
        enabled: bool = DEFAULT_TIME_BASED_MC_ENABLED,
        gap_threshold_minutes: int = DEFAULT_GAP_THRESHOLD_MINUTES,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ):
        self.enabled = enabled
        self.gap_threshold_minutes = gap_threshold_minutes
        self.keep_recent = keep_recent


_DEFAULT_MC_CONFIG = TimeBasedMCConfig()


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------


def count_tool_result_tokens(block: Union[ToolResultBlock, dict[str, Any]]) -> int:
    """Count estimated tokens in a tool_result block (typed or dict)."""
    if isinstance(block, ToolResultBlock):
        content = block.content
    elif isinstance(block, dict):
        content = block.get("content", "")
    else:
        return 0

    if isinstance(content, str):
        return rough_token_count(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in ("image", "document"):
                    total += IMAGE_TOKEN_SIZE
                elif item.get("type") == "text":
                    total += rough_token_count(item.get("text", ""))
            elif isinstance(item, (ImageBlock, DocumentBlock)):
                total += IMAGE_TOKEN_SIZE
            elif isinstance(item, TextBlock):
                total += rough_token_count(item.text)
        return total
    return 0


def is_compactable_tool(tool_name: str) -> bool:
    """Check if a tool is eligible for microcompact."""
    return tool_name in COMPACTABLE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Image / document stripping (works on raw dicts — API message format)
# ---------------------------------------------------------------------------


def strip_images_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Strip image/document blocks from user messages.

    Replaces them with [image] / [document] text markers.
    Operates on raw API-format dicts.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "user":
            result.append(msg)
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            result.append(msg)
            continue

        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content: list[Any] = []
        changed = False
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type", "")
            if block_type == "image":
                changed = True
                new_content.append({"type": "text", "text": "[image]"})
            elif block_type == "document":
                changed = True
                new_content.append({"type": "text", "text": "[document]"})
            elif block_type == "tool_result" and isinstance(block.get("content"), list):
                # Strip nested images/documents from tool_result content
                new_tool_content: list[Any] = []
                tool_changed = False
                for item in block["content"]:
                    if isinstance(item, dict) and item.get("type") in ("image", "document"):
                        tool_changed = True
                        new_tool_content.append({"type": "text", "text": f"[{item['type']}]"})
                    else:
                        new_tool_content.append(item)
                if tool_changed:
                    changed = True
                    new_block = {**block, "content": new_tool_content}
                    new_content.append(new_block)
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if changed:
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    return result


def strip_images_from_typed_messages(messages: list[Message]) -> list[Message]:
    """
    Strip image/document blocks from typed ``Message`` objects.

    Returns a new list; original messages are not mutated.
    """
    result: list[Message] = []
    for msg in messages:
        if msg.role != "user" or not isinstance(msg.content, list):
            result.append(msg)
            continue

        new_content: list[ContentBlock] = []
        changed = False
        for block in msg.content:
            if isinstance(block, ImageBlock):
                changed = True
                new_content.append(TextBlock(text="[image]"))
            elif isinstance(block, DocumentBlock):
                changed = True
                new_content.append(TextBlock(text="[document]"))
            elif isinstance(block, ToolResultBlock) and isinstance(block.content, list):
                # Check nested content
                new_nested: list[Any] = []
                nested_changed = False
                for item in block.content:
                    if isinstance(item, dict) and item.get("type") in ("image", "document"):
                        nested_changed = True
                        new_nested.append({"type": "text", "text": f"[{item['type']}]"})
                    elif isinstance(item, (ImageBlock, DocumentBlock)):
                        nested_changed = True
                        type_name = "image" if isinstance(item, ImageBlock) else "document"
                        new_nested.append({"type": "text", "text": f"[{type_name}]"})
                    else:
                        new_nested.append(item)
                if nested_changed:
                    changed = True
                    new_content.append(ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=new_nested,
                        is_error=block.is_error,
                    ))
                else:
                    new_content.append(block)
            elif isinstance(block, dict):
                bt = block.get("type", "")
                if bt == "image":
                    changed = True
                    new_content.append(TextBlock(text="[image]"))
                elif bt == "document":
                    changed = True
                    new_content.append(TextBlock(text="[document]"))
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if changed:
            new_msg = UserMessage(
                content=new_content,
                uuid=msg.uuid,
                timestamp=msg.timestamp,
                isMeta=msg.isMeta,
            )
            result.append(new_msg)
        else:
            result.append(msg)

    return result


# ---------------------------------------------------------------------------
# Core microcompact — typed Message objects
# ---------------------------------------------------------------------------


def _collect_compactable_ids_typed(messages: list[Message]) -> list[str]:
    """Collect tool_use IDs for compactable tools from typed messages."""
    ids: list[str] = []
    for msg in messages:
        if msg.role != "assistant" or not isinstance(msg.content, list):
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and is_compactable_tool(block.name):
                ids.append(block.id)
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                if is_compactable_tool(block.get("name", "")):
                    ids.append(block.get("id", ""))
    return ids


def _get_last_assistant_timestamp(messages: list[Message]) -> float | None:
    """Return the POSIX timestamp of the last assistant message, or None."""
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.timestamp:
            try:
                dt = datetime.fromisoformat(msg.timestamp)
                return dt.timestamp()
            except (ValueError, TypeError):
                pass
    return None


def _should_time_based_microcompact(
    messages: list[Message],
    config: TimeBasedMCConfig,
) -> bool:
    """Check if time-based microcompact should trigger."""
    if not config.enabled or len(messages) < 2:
        return False
    last_ts = _get_last_assistant_timestamp(messages)
    if last_ts is None:
        return False
    gap_minutes = (time.time() - last_ts) / 60.0
    return gap_minutes >= config.gap_threshold_minutes


def microcompact_typed_messages(
    messages: list[Message],
    keep_recent: int = DEFAULT_KEEP_RECENT,
    *,
    time_config: TimeBasedMCConfig | None = None,
    force: bool = False,
) -> tuple[list[Message], int]:
    """
    Microcompact typed ``Message`` objects.

    Clears content from compactable tool results beyond the most recent
    ``keep_recent`` ones.  Optionally gated by a time-based trigger:
    only fires when the gap since the last assistant message exceeds a
    threshold (server prompt cache has expired).

    Args:
        messages: Conversation messages.
        keep_recent: Number of recent compactable results to keep.
        time_config: Time-based trigger config.  ``None`` = always run.
        force: If ``True``, skip time-based gating.

    Returns:
        ``(modified_messages, tokens_saved)``
    """
    if time_config is not None and not force:
        if not _should_time_based_microcompact(messages, time_config):
            return list(messages), 0

    compactable_ids = _collect_compactable_ids_typed(messages)

    if len(compactable_ids) <= keep_recent:
        return list(messages), 0

    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(compactable_ids[:-keep_recent])

    tokens_saved = 0
    result: list[Message] = []

    for msg in messages:
        if msg.role != "user" or not isinstance(msg.content, list):
            result.append(msg)
            continue

        new_content: list[ContentBlock] = []
        changed = False

        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                if (
                    block.tool_use_id in clear_set
                    and block.content != CLEARED_MESSAGE
                ):
                    saved = count_tool_result_tokens(block)
                    tokens_saved += saved
                    changed = True
                    new_content.append(ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=CLEARED_MESSAGE,
                        is_error=block.is_error,
                    ))
                else:
                    new_content.append(block)
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                tuid = block.get("tool_use_id", "")
                if tuid in clear_set and block.get("content") != CLEARED_MESSAGE:
                    saved = count_tool_result_tokens(block)
                    tokens_saved += saved
                    changed = True
                    new_content.append({**block, "content": CLEARED_MESSAGE})
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if changed:
            new_msg = UserMessage(
                content=new_content,
                uuid=msg.uuid,
                timestamp=msg.timestamp,
                isMeta=msg.isMeta,
            )
            result.append(new_msg)
        else:
            result.append(msg)

    return result, tokens_saved


# ---------------------------------------------------------------------------
# Backward-compatible dict-based API (used by compact_service and tests)
# ---------------------------------------------------------------------------


def microcompact_messages(
    messages: list[dict[str, Any]],
    keep_recent: int = 3,
) -> tuple[list[dict[str, Any]], int]:
    """
    Lightweight compact of old tool results (dict-based, backward compat).

    Clears content from compactable tool results beyond the most recent
    ``keep_recent`` ones.

    Returns:
        Tuple of (modified_messages, tokens_saved)
    """
    # Collect compactable tool_use IDs in order
    compactable_ids: list[str] = []
    for msg in messages:
        content = msg.get("content", [])
        if msg.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and is_compactable_tool(block.get("name", ""))
                ):
                    compactable_ids.append(block.get("id", ""))

    # Keep the last N compactable tool results
    if len(compactable_ids) <= keep_recent:
        return list(messages), 0

    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(compactable_ids[:-keep_recent])

    tokens_saved = 0
    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") != "user":
            result.append(msg)
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            result.append(msg)
            continue

        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content: list[Any] = []
        changed = False
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in clear_set
                and block.get("content") != CLEARED_MESSAGE
            ):
                saved = count_tool_result_tokens(block)
                tokens_saved += saved
                changed = True
                new_content.append({**block, "content": CLEARED_MESSAGE})
            else:
                new_content.append(block)

        if changed:
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    return result, tokens_saved
