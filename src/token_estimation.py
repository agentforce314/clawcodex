"""Token estimation utilities — mirrors TypeScript tokenEstimation.ts.

Provides rough token counting for messages and content blocks, plus
accurate tiktoken-based counting when available. API-based counting
is also supported for precise results.

Ch17 round-2: text- and block-level memoization layer. The compact
pipeline (`services/compact/compact.py:319,453,512`), reactive compact
(`services/compact/reactive_compact.py:183,223`), and context analyzer
(`context_system/context_analyzer.py`) call ``count_tokens`` /
``count_messages_tokens`` repeatedly against largely-overlapping
content within a single turn. Each tiktoken ``encode`` call is
~100 us–10 ms; without the cache, a single compaction event pays
that cost 3-5× over the same message list. The cache key is the
content's hash, so identical content always hits regardless of which
caller invoked it.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
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


# ---------------------------------------------------------------------------
# Memoization layer (ch17 round-2)
# ---------------------------------------------------------------------------

_MAX_TEXT_CACHE = 4096
_MAX_BLOCK_CACHE = 4096


class _TokenCountCache:
    """Content-keyed LRU cache for token counts.

    Backed by ``OrderedDict`` rather than ``functools.lru_cache`` so we
    can:
      - Reset for tests via ``reset_token_cache()``.
      - Expose hit/miss counters via ``get_token_cache_stats()``.
      - Bound by size, not by function-argument hashability quirks.

    Keys are the content itself (a string or any hashable value),
    NOT ``hash(content)`` — Python's ``hash()`` is a 64-bit integer
    and collisions between distinct inputs are possible (birthday
    paradox is real at 4096 entries). Using the content directly as
    the dict key lets Python's dict implementation handle collisions
    correctly via ``__eq__`` after hashing.

    Thread safety: Python's GIL serializes simple ``__getitem__`` /
    ``__setitem__`` operations. Failure mode under concurrent writes is
    a missed cache hit or a stale eviction, never an incorrect token
    count.
    """

    __slots__ = ("_cache", "_max_size", "hits", "misses")

    def __init__(self, max_size: int) -> None:
        self._cache: OrderedDict[Any, int] = OrderedDict()
        self._max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Optional[int]:
        # Sentinel pattern so that a legitimately-cached 0 is still a hit.
        # (We don't store None, but a future caller might cache 0 if they
        # bypass the count_tokens short-circuit.)
        sentinel: Any = _CACHE_MISS_SENTINEL
        cached = self._cache.get(key, sentinel)
        if cached is sentinel:
            self.misses += 1
            return None
        self.hits += 1
        self._cache.move_to_end(key)
        return cached  # type: ignore[return-value]

    def put(self, key: Any, value: int) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
            return
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            # Evict the least-recently used entry (front of the OrderedDict).
            self._cache.popitem(last=False)

    def reset(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0

    def size(self) -> int:
        return len(self._cache)


# Module-level sentinel for the cache-miss check. Defined as a module-level
# object so equality is identity-based and never accidentally satisfied by
# a stored value (no token count is this object).
_CACHE_MISS_SENTINEL: object = object()


_TEXT_CACHE = _TokenCountCache(_MAX_TEXT_CACHE)
_BLOCK_CACHE = _TokenCountCache(_MAX_BLOCK_CACHE)


def get_token_cache_stats() -> dict[str, int]:
    """Return hit/miss counters and current cache sizes.

    Useful for observability (e.g., a future ``/tokens`` debug command)
    and for tests that need to verify cache behaviour without timing.
    """
    return {
        "text_cache_hits": _TEXT_CACHE.hits,
        "text_cache_misses": _TEXT_CACHE.misses,
        "block_cache_hits": _BLOCK_CACHE.hits,
        "block_cache_misses": _BLOCK_CACHE.misses,
        "text_cache_size": _TEXT_CACHE.size(),
        "block_cache_size": _BLOCK_CACHE.size(),
    }


def reset_token_cache() -> None:
    """Clear both caches and reset counters. Primarily for test isolation."""
    _TEXT_CACHE.reset()
    _BLOCK_CACHE.reset()


def count_tokens(text: str) -> int:
    if not text:
        return 0
    cached = _TEXT_CACHE.get(text)
    if cached is not None:
        return cached
    encoder = _get_encoder()
    if encoder is not None:
        try:
            result = len(encoder.encode(text))
            _TEXT_CACHE.put(text, result)
            return result
        except Exception:
            pass
    result = max(1, len(text) // 4)
    _TEXT_CACHE.put(text, result)
    return result


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


def _block_cache_key(block: Any) -> Optional[tuple]:
    """Stable cache key for a content block.

    Returns ``None`` when the block isn't cacheable (e.g., a custom
    object whose JSON-serialisation raises). Returning ``None`` short-
    circuits the cache lookup; the caller falls through to the
    uncached compute path. No exception ever propagates to the caller.

    Key shape (used as a dict key — Python's dict handles hash
    collisions correctly via ``__eq__``):
      - ``str`` blocks → ``("str", text)``
      - ``dict`` blocks → ``(block_type, json_str)`` where
        ``json_str`` is the deterministic JSON projection of the dict.
        Dict insertion order is preserved in Python 3.7+, so two
        structurally-identical dicts produce byte-identical JSON.
      - other → ``None``
    """
    if isinstance(block, str):
        return ("str", block)
    if isinstance(block, dict):
        block_type = block.get("type", "")
        try:
            payload = _json_stringify(block)
        except Exception:
            return None
        return (block_type, payload)
    return None


def _rough_token_count_estimation_for_block_impl(block: Any) -> int:
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


def rough_token_count_estimation_for_block(block: Any) -> int:
    key = _block_cache_key(block)
    if key is not None:
        cached = _BLOCK_CACHE.get(key)
        if cached is not None:
            return cached
    result = _rough_token_count_estimation_for_block_impl(block)
    if key is not None:
        _BLOCK_CACHE.put(key, result)
    return result


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

        from src.services.api.custom_headers import get_anthropic_custom_headers
        client = anthropic.AsyncAnthropic(
            default_headers=get_anthropic_custom_headers() or None
        )
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
