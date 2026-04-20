"""Token estimation utilities — mirrors TypeScript tokenEstimation.ts.

Provides rough token counting for messages and content blocks, plus
accurate tiktoken-based counting when available. API-based counting
is also supported for precise results.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

_encoder_cache: Optional[object] = None
_encoder_loaded: bool = False


def _load_tiktoken(encoding: str = "cl100k_base") -> Optional[object]:
    try:
        import tiktoken
        return tiktoken.get_encoding(encoding)
    except Exception:
        return None


def _get_encoder() -> Optional[object]:
    global _encoder_cache, _encoder_loaded
    if not _encoder_loaded:
        _encoder_cache = _load_tiktoken()
        _encoder_loaded = True
    return _encoder_cache


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def rough_token_count_estimation(content: str, bytes_per_token: int = 4) -> int:
    return round(len(content) / bytes_per_token)


def bytes_per_token_for_file_type(file_extension: str) -> int:
    if file_extension in ("json", "jsonl", "jsonc"):
        return 2
    return 4


def rough_token_count_estimation_for_file_type(
    content: str, file_extension: str
) -> int:
    return rough_token_count_estimation(
        content, bytes_per_token_for_file_type(file_extension)
    )


def rough_token_count_estimation_for_messages(
    messages: Sequence[Any],
) -> int:
    total = 0
    for message in messages:
        total += rough_token_count_estimation_for_message(message)
    return total


def rough_token_count_estimation_for_message(message: Any) -> int:
    msg_type = _get_type(message)

    if msg_type in ("assistant", "user"):
        content = _get_content(message)
        if content is not None:
            return rough_token_count_estimation_for_content(content)

    if msg_type == "attachment":
        attachment = (
            message.get("attachment")
            if isinstance(message, dict)
            else getattr(message, "attachment", None)
        )
        if attachment is not None:
            return _estimate_attachment_tokens(attachment)

    return 0


def rough_token_count_estimation_for_content(
    content: str | list[Any] | None,
) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    total = 0
    for block in content:
        total += rough_token_count_estimation_for_block(block)
    return total


def rough_token_count_estimation_for_block(block: Any) -> int:
    if isinstance(block, str):
        return rough_token_count_estimation(block)

    block_type = _get_block_type(block)

    if block_type == "text":
        text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
        return rough_token_count_estimation(str(text))

    if block_type in ("image", "document"):
        return 2000

    if block_type == "tool_result":
        inner = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
        return rough_token_count_estimation_for_content(inner)

    if block_type == "tool_use":
        name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
        inp = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
        return rough_token_count_estimation(str(name) + _json_stringify(inp))

    if block_type == "thinking":
        thinking = block.get("thinking", "") if isinstance(block, dict) else getattr(block, "thinking", "")
        return rough_token_count_estimation(str(thinking))

    if block_type == "redacted_thinking":
        data = block.get("data", "") if isinstance(block, dict) else getattr(block, "data", "")
        return rough_token_count_estimation(str(data))

    return rough_token_count_estimation(_json_stringify(block))


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        total += count_tokens(role) + 4

        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    total += count_tokens(block.get("text", ""))
                elif block_type == "tool_use":
                    total += count_tokens(block.get("name", ""))
                    total += count_tokens(str(block.get("input", {})))
                elif block_type == "tool_result":
                    total += count_tokens(str(block.get("content", "")))
                elif block_type in ("image", "document"):
                    total += 2000
                elif block_type == "thinking":
                    total += count_tokens(block.get("thinking", ""))
                elif block_type == "redacted_thinking":
                    total += count_tokens(block.get("data", ""))
                else:
                    total += count_tokens(str(block))
    return total


async def count_tokens_with_api(content: str) -> int | None:
    if not content:
        return 0
    return await count_messages_tokens_with_api(
        [{"role": "user", "content": content}], []
    )


async def count_messages_tokens_with_api(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int | None:
    try:
        import anthropic

        client = anthropic.AsyncAnthropic()
        response = await client.beta.messages.count_tokens(
            model="claude-sonnet-4-20250514",
            messages=messages,
            tools=tools if tools else [],
        )
        if hasattr(response, "input_tokens"):
            return response.input_tokens
        return None
    except Exception as e:
        logger.debug("API token counting failed: %s", e)
        return None


rough_token_count = rough_token_count_estimation


def _get_type(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("type", ""))
    return str(getattr(message, "type", ""))


def _get_content(message: Any) -> str | list[Any] | None:
    if isinstance(message, dict):
        msg_inner = message.get("message")
        if isinstance(msg_inner, dict):
            return msg_inner.get("content")
        return message.get("content")
    msg_inner = getattr(message, "message", None)
    if msg_inner is not None:
        return getattr(msg_inner, "content", None)
    return getattr(message, "content", None)


def _get_block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type", ""))
    return str(getattr(block, "type", ""))


def _json_stringify(obj: Any) -> str:
    try:
        return json.dumps(obj, separators=(",", ":"), default=str)
    except Exception:
        return str(obj)


def _estimate_attachment_tokens(attachment: Any) -> int:
    if isinstance(attachment, dict):
        content = attachment.get("content", "")
    else:
        content = getattr(attachment, "content", "")
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    if isinstance(content, list):
        return rough_token_count_estimation_for_content(content)
    return 0


# ---------------------------------------------------------------------------
# Extended estimation functions (R2-WS-9)
# ---------------------------------------------------------------------------


def estimate_tool_schema_tokens(tool_schema: dict[str, Any]) -> int:
    """Estimate tokens for a tool's JSON schema definition.

    Used for deferred tool loading decisions — if a tool schema is large,
    it may be worth deferring it.
    """
    serialized = _json_stringify(tool_schema)
    return rough_token_count_estimation(serialized, bytes_per_token=2)


def estimate_system_prompt_tokens(prompt: str) -> int:
    """Estimate tokens for a system prompt section."""
    return count_tokens(prompt)


def estimate_system_prompt_sections_tokens(sections: dict[str, str]) -> dict[str, int]:
    """Estimate tokens per section of a system prompt."""
    return {name: count_tokens(text) for name, text in sections.items()}


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimate tokens for an image based on dimensions.

    Uses the Claude vision pricing formula:
    tokens = ceil(width / 32) * ceil(height / 32) * 3
    Minimum: 85 tokens
    """
    import math
    w_blocks = math.ceil(width / 32)
    h_blocks = math.ceil(height / 32)
    return max(85, w_blocks * h_blocks * 3)


def estimate_cache_aware_tokens(
    total_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict[str, int]:
    """Break down token counts with cache awareness.

    Returns dict with:
    - uncached_tokens: tokens that were not cached
    - cache_read_tokens: tokens read from cache (cheaper)
    - cache_creation_tokens: tokens that created new cache entries
    - effective_tokens: weighted token count for cost estimation
    """
    uncached = max(0, total_tokens - cache_read_tokens - cache_creation_tokens)
    # Cache reads are ~90% cheaper, cache creation ~25% more expensive
    effective = uncached + int(cache_read_tokens * 0.1) + int(cache_creation_tokens * 1.25)
    return {
        "uncached_tokens": uncached,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "effective_tokens": effective,
    }


def rough_token_count_estimation_per_block_type(
    blocks: list[Any],
) -> dict[str, int]:
    """Count tokens per block type for a list of content blocks."""
    by_type: dict[str, int] = {}
    for block in blocks:
        btype = _get_block_type(block)
        tokens = rough_token_count_estimation_for_block(block)
        by_type[btype] = by_type.get(btype, 0) + tokens
    return by_type
