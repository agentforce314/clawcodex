"""Extended message utilities matching TypeScript utils/messages.ts.

Provides advanced normalization, creation variants, content helpers,
thinking block handling, and message predicates.
"""

from __future__ import annotations

from typing import Any

from ..types.content_blocks import (
    ContentBlock,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    RedactedThinkingBlock,
    ImageBlock,
    content_block_to_dict,
)
from ..types.messages import (
    AssistantMessage,
    Message,
    MessageContent,
    MessageLike,
    SystemMessage,
    UserMessage,
    create_assistant_message,
    create_system_message,
    create_user_message,
    _get_field,
    NO_CONTENT_MESSAGE,
    SYNTHETIC_MODEL,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
)

# ---------------------------------------------------------------------------
# Message predicates
# ---------------------------------------------------------------------------


def is_synthetic(message: Message) -> bool:
    """Check if a message was synthetically generated (not from API)."""
    if isinstance(message, AssistantMessage):
        return message.model == SYNTHETIC_MODEL or message.isApiErrorMessage
    return message.isMeta or message.isVirtual


def is_attachment(message: Message) -> bool:
    """Check if a message is an attachment message."""
    return message.type == "attachment"


def is_compact_boundary(message: Message) -> bool:
    """Check if a message is a compact boundary marker."""
    return message.isCompactSummary


def is_tool_result(message: Message) -> bool:
    """Check if a message contains tool results."""
    if message.role != "user":
        return False
    content = message.content
    if isinstance(content, list):
        return any(
            getattr(b, "type", None) == "tool_result"
            or (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in content
        )
    return False


def is_thinking_block(block: Any) -> bool:
    """Check if a content block is a thinking block."""
    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
    return btype in ("thinking", "redacted_thinking")


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------


def get_content_text(message: Message) -> str:
    """Extract all text content from a message."""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, ThinkingBlock):
            parts.append(block.thinking)
    return "\n".join(parts)


def count_tool_calls(message: Message) -> int:
    """Count the number of tool_use blocks in a message."""
    content = message.content
    if not isinstance(content, list):
        return 0
    return sum(
        1 for b in content
        if getattr(b, "type", None) == "tool_use"
        or (isinstance(b, dict) and b.get("type") == "tool_use")
    )


def get_messages_after_compact_boundary(messages: list[Message]) -> list[Message]:
    """Get messages after the last compact boundary."""
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary(messages[i]):
            return messages[i + 1:]
    return list(messages)


# ---------------------------------------------------------------------------
# Thinking block handling
# ---------------------------------------------------------------------------


def strip_thinking_blocks(content: list[ContentBlock]) -> list[ContentBlock]:
    """Remove thinking and redacted_thinking blocks from content."""
    return [b for b in content if not is_thinking_block(b)]


def preserve_thinking_blocks(content: list[ContentBlock]) -> list[ContentBlock]:
    """Keep only thinking blocks from content."""
    return [b for b in content if is_thinking_block(b)]


# ---------------------------------------------------------------------------
# Enhanced normalization
# ---------------------------------------------------------------------------


def normalize_messages_for_api_enhanced(
    messages: list[MessageLike],
    *,
    strip_thinking: bool = True,
    strip_system: bool = True,
    pair_tool_results: bool = True,
) -> list[dict[str, Any]]:
    """Advanced normalization for API submission.

    Additional features over the base normalize_messages_for_api:
    - Thinking block pruning (strip_thinking)
    - System message stripping (strip_system)
    - Tool result pairing validation (pair_tool_results)
    - Redacted thinking block handling
    """
    normalized: list[dict[str, Any]] = []
    seen_tool_use_ids: set[str] = set()

    for message in messages:
        msg_type = _get_field(message, "type", "user")

        # Skip progress messages
        if msg_type == "progress":
            continue

        # Skip virtual messages
        if _get_field(message, "isVirtual", False):
            continue

        # System message handling
        if msg_type == "system":
            if strip_system:
                subtype = _get_field(message, "subtype", None)
                if subtype != "local_command":
                    continue
            else:
                continue

        role = _get_field(message, "role", "user")
        api_role = role if role in ("user", "assistant") else "user"
        content = _get_field(message, "content", "")

        if isinstance(content, str):
            api_content: str | list[dict[str, Any]] = content
        elif isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for block in content:
                block_dict = content_block_to_dict(block) if not isinstance(block, dict) else block
                btype = block_dict.get("type", "")

                # Strip thinking blocks if requested
                if strip_thinking and btype in ("thinking", "redacted_thinking"):
                    continue

                # Track tool_use IDs
                if btype == "tool_use":
                    bid = block_dict.get("id", "")
                    if bid:
                        seen_tool_use_ids.add(bid)

                blocks.append(block_dict)
            api_content = blocks if blocks else [{"type": "text", "text": NO_CONTENT_MESSAGE}]
        else:
            api_content = str(content)

        entry = {"role": api_role, "content": api_content}

        # Merge consecutive same-role messages
        if normalized and normalized[-1]["role"] == api_role == "user":
            existing_content = normalized[-1]["content"]
            new_content = api_content
            if isinstance(existing_content, str):
                existing_content = [{"type": "text", "text": existing_content}]
            if isinstance(new_content, str):
                new_content = [{"type": "text", "text": new_content}]
            normalized[-1]["content"] = existing_content + new_content
        else:
            normalized.append(entry)

    # Validate tool result pairing
    if pair_tool_results:
        _ensure_tool_result_pairing(normalized, seen_tool_use_ids)

    return normalized


def _ensure_tool_result_pairing(
    messages: list[dict[str, Any]],
    seen_tool_use_ids: set[str],
) -> None:
    """Ensure every tool_use has a matching tool_result.

    Adds synthetic tool_result for orphaned tool_use blocks.
    """
    responded_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    responded_ids.add(block.get("tool_use_id", ""))

    orphaned = seen_tool_use_ids - responded_ids
    if orphaned and messages:
        # Find the last assistant message and add synthetic results after it
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "assistant":
                synthetic_blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                    }
                    for tid in orphaned
                ]
                # Insert as a user message right after the assistant
                if i + 1 < len(messages) and messages[i + 1]["role"] == "user":
                    existing = messages[i + 1]["content"]
                    if isinstance(existing, str):
                        existing = [{"type": "text", "text": existing}]
                    messages[i + 1]["content"] = synthetic_blocks + existing
                else:
                    messages.insert(i + 1, {
                        "role": "user",
                        "content": synthetic_blocks,
                    })
                break


# ---------------------------------------------------------------------------
# Create variants
# ---------------------------------------------------------------------------


def create_user_tool_result_message(
    tool_use_id: str,
    content: str,
    *,
    is_error: bool = False,
) -> UserMessage:
    """Create a user message containing a tool result."""
    return create_user_message(
        content=[
            ToolResultBlock(
                type="tool_result",
                tool_use_id=tool_use_id,
                content=content,
                is_error=is_error,
            )
        ],
    )


def create_user_command_input_message(text: str) -> UserMessage:
    """Create a user message from command input."""
    return create_user_message(content=text, isMeta=True)


def create_assistant_compact_boundary_message(summary: str) -> AssistantMessage:
    """Create a compact boundary marker message."""
    return create_assistant_message(
        content=summary,
        isVirtual=False,
    )


def create_system_local_command_message(text: str) -> SystemMessage:
    """Create a system message for local command output."""
    return create_system_message(text, subtype="local_command")


def create_system_compact_boundary_message(summary: str) -> SystemMessage:
    """Create a system compact boundary marker."""
    return create_system_message(
        summary,
        subtype="compact_boundary",
    )


def create_system_max_turns_message(max_turns: int) -> SystemMessage:
    """Create a system message for max turns reached."""
    return create_system_message(
        f"Maximum number of turns ({max_turns}) reached. Stopping.",
        level="warning",
        subtype="max_turns",
        preventContinuation=True,
    )
