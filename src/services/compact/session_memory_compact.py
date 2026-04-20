"""
Session-memory-based compaction.

Port of ``typescript/src/services/compact/sessionMemoryCompact.ts``.

Determines a safe split point for partial compaction, ensuring that
tool_use / tool_result pairs and thinking blocks are not broken across
the boundary. Uses token-based thresholds matching the TypeScript reference.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...types.content_blocks import ToolResultBlock, ToolUseBlock, TextBlock
from ...types.messages import Message
from ...token_estimation import (
    rough_token_count,
    rough_token_count_estimation_for_message,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration thresholds (mirroring TS constants)
# ---------------------------------------------------------------------------

@dataclass
class SessionMemoryCompactConfig:
    """Token-based thresholds for session memory compaction.

    Matches TypeScript DEFAULT_SM_COMPACT_CONFIG.
    """
    min_tokens: int = 10_000
    min_text_block_messages: int = 5
    max_tokens: int = 40_000


DEFAULT_SM_COMPACT_CONFIG = SessionMemoryCompactConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def has_text_blocks(message: Message) -> bool:
    """Check if a message contains meaningful text content."""
    content = message.content
    if isinstance(content, str):
        return len(content) > 0
    if isinstance(content, list):
        for block in content:
            if isinstance(block, TextBlock):
                return True
            if isinstance(block, dict) and block.get("type") == "text":
                return True
    return False


def _estimate_message_tokens(message: Message) -> int:
    """Rough token estimate for a single message.

    Delegates to the shared content-block-aware estimator and pads by 4/3
    to match TS ``estimateMessageTokens`` conservativeness.
    """
    base = rough_token_count_estimation_for_message(message)
    return int(base * 4 / 3) if base else 0


def _get_tool_result_ids(message: Message) -> list[str]:
    """Return tool_use_ids from tool_result blocks in a user message."""
    if message.role != "user":
        return []
    content = message.content
    if not isinstance(content, list):
        return []
    ids: list[str] = []
    for block in content:
        if isinstance(block, ToolResultBlock):
            ids.append(block.tool_use_id)
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            ids.append(block.get("tool_use_id", ""))
    return ids


def _has_tool_use_with_ids(message: Message, tool_use_ids: set[str]) -> bool:
    """Check if an assistant message contains tool_use blocks with any of the given ids."""
    if message.role != "assistant":
        return False
    content = message.content
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, ToolUseBlock) and block.id in tool_use_ids:
            return True
        if isinstance(block, dict) and block.get("type") == "tool_use":
            if block.get("id", "") in tool_use_ids:
                return True
    return False


def _get_message_id(message: Message) -> str | None:
    """Return the API message id (not uuid) for grouping streaming chunks."""
    if hasattr(message, "message_id"):
        return message.message_id
    if hasattr(message, "_api_id"):
        return message._api_id
    return None


# ---------------------------------------------------------------------------
# Core API: adjust index to preserve invariants
# ---------------------------------------------------------------------------

def adjust_index_to_preserve_api_invariants(
    messages: list[Message],
    index: int,
) -> int:
    """
    Adjust *index* so the split does not break:
    1. tool_use / tool_result pairs
    2. Thinking blocks that share the same message.id with kept assistant messages

    Port of ``adjustIndexToPreserveAPIInvariants`` in sessionMemoryCompact.ts.
    """
    if index <= 0 or index >= len(messages):
        return index

    adjusted = index

    # Step 1: Handle tool_use/tool_result pairs
    # Collect tool_result IDs from ALL messages in the kept range
    all_tool_result_ids: list[str] = []
    for i in range(adjusted, len(messages)):
        all_tool_result_ids.extend(_get_tool_result_ids(messages[i]))

    if all_tool_result_ids:
        # Collect tool_use IDs already in the kept range
        tool_use_ids_in_kept: set[str] = set()
        for i in range(adjusted, len(messages)):
            msg = messages[i]
            if msg.role == "assistant" and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_use_ids_in_kept.add(block.id)
                    elif isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_use_ids_in_kept.add(block.get("id", ""))

        # Only look for tool_uses NOT already in the kept range
        needed_ids = set(
            tid for tid in all_tool_result_ids
            if tid not in tool_use_ids_in_kept
        )

        # Find the assistant message(s) with matching tool_use blocks
        i = adjusted - 1
        while i >= 0 and needed_ids:
            msg = messages[i]
            if _has_tool_use_with_ids(msg, needed_ids):
                adjusted = i
                # Remove found tool_use_ids from the set
                if msg.role == "assistant" and isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock) and block.id in needed_ids:
                            needed_ids.discard(block.id)
                        elif isinstance(block, dict) and block.get("type") == "tool_use":
                            needed_ids.discard(block.get("id", ""))
            i -= 1

    # Step 2: Handle thinking blocks that share message.id with kept assistant messages
    message_ids_in_kept: set[str] = set()
    for i in range(adjusted, len(messages)):
        msg = messages[i]
        if msg.role == "assistant":
            mid = _get_message_id(msg)
            if mid:
                message_ids_in_kept.add(mid)

    # Look backwards for assistant messages with the same message.id
    for i in range(adjusted - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant":
            mid = _get_message_id(msg)
            if mid and mid in message_ids_in_kept:
                adjusted = i

    return adjusted


# ---------------------------------------------------------------------------
# Core API: calculate messages to keep (token-based)
# ---------------------------------------------------------------------------

def calculate_messages_to_keep_index(
    messages: list[Message],
    last_summarized_index: int,
    config: SessionMemoryCompactConfig | None = None,
) -> int:
    """
    Calculate the starting index for messages to keep after compaction.

    Starts from ``last_summarized_index``, then expands backwards to meet:
    - At least ``config.min_tokens`` tokens
    - At least ``config.min_text_block_messages`` messages with text blocks
    Stops expanding if ``config.max_tokens`` is reached.

    Port of ``calculateMessagesToKeepIndex`` in sessionMemoryCompact.ts.
    """
    if not messages:
        return 0

    if config is None:
        config = DEFAULT_SM_COMPACT_CONFIG

    # Start from the message after last_summarized_index
    start_index = (
        last_summarized_index + 1
        if last_summarized_index >= 0
        else len(messages)
    )

    # Calculate current tokens and text-block message count
    total_tokens = 0
    text_block_count = 0
    for i in range(start_index, len(messages)):
        total_tokens += _estimate_message_tokens(messages[i])
        if has_text_blocks(messages[i]):
            text_block_count += 1

    # Check if we already hit the max cap
    if total_tokens >= config.max_tokens:
        return adjust_index_to_preserve_api_invariants(messages, start_index)

    # Check if we already meet both minimums
    if (
        total_tokens >= config.min_tokens
        and text_block_count >= config.min_text_block_messages
    ):
        return adjust_index_to_preserve_api_invariants(messages, start_index)

    # Find the floor: don't expand past the last compact boundary
    from ...compact_service.messages import is_compact_boundary_message
    floor = 0
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            floor = i + 1
            break

    # Expand backwards until we meet both minimums or hit max cap
    for i in range(start_index - 1, floor - 1, -1):
        msg_tokens = _estimate_message_tokens(messages[i])
        total_tokens += msg_tokens
        if has_text_blocks(messages[i]):
            text_block_count += 1
        start_index = i

        if total_tokens >= config.max_tokens:
            break

        if (
            total_tokens >= config.min_tokens
            and text_block_count >= config.min_text_block_messages
        ):
            break

    return adjust_index_to_preserve_api_invariants(messages, start_index)


# ---------------------------------------------------------------------------
# Legacy count-based API (backward compatibility)
# ---------------------------------------------------------------------------

def try_session_memory_compaction(
    messages: list[Message],
    target_keep_count: int,
    config: SessionMemoryCompactConfig | None = None,
) -> tuple[list[Message], list[Message]]:
    """
    Split messages into [summarize, keep] based on target_keep_count.

    This is a simplified backward-compatible API. For the full token-based
    approach, use ``calculate_messages_to_keep_index()`` directly.
    """
    if config is None:
        config = SessionMemoryCompactConfig()

    n = len(messages)
    if n == 0:
        return [], list(messages)

    # Compute a split point from target_keep_count
    split = max(0, n - target_keep_count)

    # Ensure we don't exceed 75% of messages
    max_summarize = int(n * 0.75)
    split = min(split, max_summarize)

    split = adjust_index_to_preserve_api_invariants(messages, split)

    if split <= 0:
        return [], list(messages)

    return list(messages[:split]), list(messages[split:])


# ---------------------------------------------------------------------------
# Session memory extraction prompt and storage
# ---------------------------------------------------------------------------

SESSION_MEMORY_PROMPT = """\
Extract the key facts, decisions, and context from this conversation that should be preserved.
Focus on:
1. What the user is trying to accomplish
2. Important decisions made
3. Files modified and why
4. Current state of the work
5. Any preferences or constraints

Return a concise bullet-point list. Do NOT include tool calls or raw code.
Respond with TEXT ONLY. No tools.
"""


@dataclass
class SessionMemoryEntry:
    fact: str
    source: str = "conversation"
    timestamp: float = 0.0


class SessionMemory:
    def __init__(self) -> None:
        self._entries: list[SessionMemoryEntry] = []

    def add(self, fact: str, source: str = "conversation") -> None:
        import time
        for entry in self._entries:
            if entry.fact.lower().strip() == fact.lower().strip():
                return
        self._entries.append(SessionMemoryEntry(
            fact=fact,
            source=source,
            timestamp=time.time(),
        ))

    def add_from_llm_response(self, response_text: str) -> int:
        added = 0
        for line in response_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("- ") or line.startswith("* ") or line.startswith("• "):
                line = line[2:].strip()
            elif line[0].isdigit() and "." in line[:4]:
                line = line.split(".", 1)[1].strip()
            if line and len(line) > 5:
                self.add(line)
                added += 1
        return added

    def deduplicate_against(self, existing_context: str) -> None:
        existing_lower = existing_context.lower()
        self._entries = [
            e for e in self._entries
            if e.fact.lower().strip() not in existing_lower
        ]

    def format_memory(self) -> str:
        if not self._entries:
            return ""
        lines = ["## Session Memory"]
        for entry in self._entries:
            lines.append(f"- {entry.fact}")
        return "\n".join(lines)

    @property
    def entries(self) -> list[SessionMemoryEntry]:
        return list(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
