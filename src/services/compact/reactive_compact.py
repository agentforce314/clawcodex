from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from ...types.messages import (
    Message,
    UserMessage,
    AssistantMessage,
    normalize_messages_for_api,
)
from ...token_estimation import count_messages_tokens

from .compact import (
    CompactContext,
    CompactionResult,
    compact_conversation,
    _fallback_summary,
)
from .session_memory_compact import (
    adjust_index_to_preserve_api_invariants,
)

logger = logging.getLogger(__name__)

MAX_EMERGENCY_RETRIES = 3
EMERGENCY_DROP_FRACTION = 0.5


@dataclass
class ReactiveCompactResult:
    compacted: bool
    messages: list[Message]
    tokens_before: int
    tokens_after: int | None = None
    error: str | None = None
    retried: bool = False


_withheld_ptl_errors: list[Exception] = []


def is_withheld_prompt_too_long(error: Exception) -> bool:
    error_str = str(error).lower()
    return (
        "prompt_too_long" in error_str
        or "prompt is too long" in error_str
        or "prompt too long" in error_str
        or "context_length_exceeded" in error_str
    )


def is_prompt_too_long_error(error: Exception) -> bool:
    return is_withheld_prompt_too_long(error)


def withhold_error(error: Exception) -> None:
    _withheld_ptl_errors.append(error)


def get_withheld_errors() -> list[Exception]:
    return list(_withheld_ptl_errors)


def clear_withheld_errors() -> None:
    _withheld_ptl_errors.clear()


def _drop_oldest_messages(
    messages: list[Message],
    fraction: float = EMERGENCY_DROP_FRACTION,
) -> list[Message]:
    if len(messages) <= 2:
        return list(messages)

    drop_count = max(1, int(len(messages) * fraction))
    candidate_index = drop_count

    candidate_index = adjust_index_to_preserve_api_invariants(
        messages, candidate_index
    )

    if candidate_index <= 0:
        return list(messages)

    return list(messages[candidate_index:])


def build_post_compact_messages(
    summary_text: str,
    remaining_messages: list[Message],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    result.append({
        "role": "user",
        "content": summary_text,
    })

    api_messages = normalize_messages_for_api(remaining_messages)

    pending_tool_ids: set[str] = set()

    for msg in api_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    pending_tool_ids.add(block.get("id", ""))

        if role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    pending_tool_ids.discard(block.get("tool_use_id", ""))

        result.append(msg)

    if pending_tool_ids:
        tool_results = []
        for tool_id in pending_tool_ids:
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "[Result from before context compaction]",
            })
        result.append({
            "role": "user",
            "content": tool_results,
        })

    _ensure_alternating_roles(result)

    return result


def _ensure_alternating_roles(messages: list[dict[str, Any]]) -> None:
    if len(messages) < 2:
        return

    i = 1
    while i < len(messages):
        if messages[i].get("role") == messages[i - 1].get("role"):
            if messages[i].get("role") == "user":
                prev_content = messages[i - 1].get("content", "")
                curr_content = messages[i].get("content", "")
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    messages[i - 1]["content"] = prev_content + "\n" + curr_content
                    messages.pop(i)
                    continue
            elif messages[i].get("role") == "assistant":
                prev_content = messages[i - 1].get("content", "")
                curr_content = messages[i].get("content", "")
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    messages[i - 1]["content"] = prev_content + "\n" + curr_content
                    messages.pop(i)
                    continue
        i += 1


async def reactive_compact(
    messages: list[Message],
    error: Exception,
    provider: Any,
    model: str,
    *,
    custom_instructions: str | None = None,
) -> ReactiveCompactResult:
    if not is_prompt_too_long_error(error):
        return ReactiveCompactResult(
            compacted=False,
            messages=list(messages),
            tokens_before=0,
            error="Not a PromptTooLong error",
        )

    withhold_error(error)

    api_messages = normalize_messages_for_api(messages)
    tokens_before = count_messages_tokens(api_messages)

    logger.info(
        "Reactive compact triggered: %d tokens, %d messages",
        tokens_before, len(messages),
    )

    ctx = CompactContext(
        provider=provider,
        model=model,
        messages=messages,
        custom_instructions=custom_instructions,
        trigger="reactive",
    )

    try:
        result = await compact_conversation(ctx)

        remaining: list[Message] = []
        if result.messages_to_keep:
            remaining = result.messages_to_keep

        return ReactiveCompactResult(
            compacted=True,
            messages=result.summary_messages + remaining,
            tokens_before=tokens_before,
            tokens_after=result.post_compact_token_count,
        )

    except Exception as compact_error:
        logger.warning("Reactive compact LLM failed: %s, trying emergency drop", compact_error)

    for attempt in range(MAX_EMERGENCY_RETRIES):
        fraction = EMERGENCY_DROP_FRACTION + (attempt * 0.15)
        truncated = _drop_oldest_messages(messages, min(fraction, 0.9))

        if len(truncated) < 2:
            break

        api_truncated = normalize_messages_for_api(truncated)
        tokens_after = count_messages_tokens(api_truncated)

        if tokens_after < tokens_before * 0.7:
            logger.info(
                "Emergency drop: %d -> %d messages, %d -> %d tokens",
                len(messages), len(truncated), tokens_before, tokens_after,
            )
            return ReactiveCompactResult(
                compacted=True,
                messages=truncated,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                retried=True,
            )

    return ReactiveCompactResult(
        compacted=False,
        messages=list(messages),
        tokens_before=tokens_before,
        error="Failed to reduce context sufficiently",
    )
