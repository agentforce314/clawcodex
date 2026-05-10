"""MCP tool-output validation: token-budget + truncation.

Phase 8 WI-8.2 (gap #13). Mirrors typescript/src/utils/mcpValidation.ts.
The chapter §"Tool Wrapping" notes: "OpenAPI-generated servers have been
observed dumping 15-60KB into ``tool.description`` — roughly 15,000 tokens
per turn for a single tool." The same hazard applies to tool *outputs*:
a misbehaving server can dump arbitrary bytes into the model's context
window. This module bounds output size in tokens (with a per-image
estimate) and truncates either string or ContentBlockParam[] outputs to
fit, so one badly-behaved server cannot exhaust the model's context.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Default cap when no env override set: 25,000 tokens per tool result.
# Mirrors TS canonical (mcpValidation.ts:25000). Big enough for typical
# tool returns and small enough to bound context-budget damage from a
# misbehaving server.
DEFAULT_MAX_MCP_OUTPUT_TOKENS = 25_000

# Image content blocks: rough token-cost estimate. Mirrors TS' 1,600
# tokens per image (planning-number, actual cost varies by resolution).
IMAGE_TOKEN_ESTIMATE = 1_600

# Per-tool-call hard cap on the textual output size in characters.
# Mirrors TS' MCPTool.maxResultSizeChars (100,000).
MAX_RESULT_SIZE_CHARS = 100_000

# Rough chars-per-token estimate for English text. Used when a more
# precise token counter (tiktoken) is unavailable. ~4 chars/token is
# the OpenAI tokenizer rule of thumb for English.
_CHARS_PER_TOKEN_ESTIMATE = 4

# Tiktoken encoder is loaded lazily on first use and cached on the
# module. ``_FAILED_ENCODER`` is a sentinel meaning "load failed; use
# the chars/token fallback forever." Avoids re-importing tiktoken and
# re-resolving the cl100k_base BPE on every estimate_text_tokens call.
_FAILED_ENCODER = object()
_tiktoken_encoder: Any = None

# Above this character length, skip tiktoken entirely and return the
# chars/4 estimate. Tiktoken's BPE is pathologically slow on repetitive
# content (~100 ms per 100k chars; degenerate inputs of repeated single
# characters can be ~100x worse). For truncation gating the chars/4
# estimate is precise enough — it's conservative (counts more tokens
# than reality) so we never under-truncate.
_TIKTOKEN_FAST_PATH_THRESHOLD = 100_000


def _get_tiktoken_encoder() -> Any:
    global _tiktoken_encoder
    if _tiktoken_encoder is _FAILED_ENCODER:
        return None
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken

        # cl100k_base is the OpenAI encoding shared with Claude's
        # tokenizer for the practical purpose of bounding output budget.
        # Imperfect but close enough for "stop the runaway server".
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        return _tiktoken_encoder
    except Exception:  # pragma: no cover - tiktoken edge cases
        _tiktoken_encoder = _FAILED_ENCODER  # type: ignore[assignment]
        return None


def get_max_mcp_output_tokens() -> int:
    """Return the operator-configurable cap on MCP tool-output tokens.

    Reads ``MCP_MAX_OUTPUT_TOKENS`` from the environment as the override.
    Falls back to ``DEFAULT_MAX_MCP_OUTPUT_TOKENS`` (25,000).
    """
    raw = os.environ.get("MCP_MAX_OUTPUT_TOKENS", "").strip()
    if not raw:
        return DEFAULT_MAX_MCP_OUTPUT_TOKENS
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        logger.warning(
            "MCP_MAX_OUTPUT_TOKENS=%r is not a positive integer; using default %d",
            raw, DEFAULT_MAX_MCP_OUTPUT_TOKENS,
        )
    return DEFAULT_MAX_MCP_OUTPUT_TOKENS


def estimate_text_tokens(text: str) -> int:
    """Rough token count for a plain string.

    Uses the module-cached tiktoken encoder for accuracy when the input
    is small enough to tokenize quickly; falls back to the chars/4
    estimate for large inputs (where precision doesn't matter for
    truncation purposes and tiktoken's BPE is slow on long repetitive
    content). Falls back to chars/4 unconditionally when tiktoken is
    unavailable or the encoder failed to load.
    """
    if not text:
        return 0
    if len(text) > _TIKTOKEN_FAST_PATH_THRESHOLD:
        return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)
    encoder = _get_tiktoken_encoder()
    if encoder is None:
        return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)
    try:
        return len(encoder.encode(text))
    except Exception:  # pragma: no cover - encode edge cases
        return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def get_content_size_estimate(content: Any) -> int:
    """Estimate the token cost of an MCP tool result's content payload.

    Accepts either:
      - a plain string (treated as text-only output), or
      - a ``list[dict]`` of ContentBlockParam-shaped dicts (text / image /
        resource / structured content).

    Image blocks contribute ``IMAGE_TOKEN_ESTIMATE`` tokens each; resource
    and unrecognized blocks are JSON-serialized and counted as text.
    """
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if not isinstance(content, list):
        return estimate_text_tokens(str(content))

    total = 0
    for block in content:
        if not isinstance(block, dict):
            total += estimate_text_tokens(str(block))
            continue
        btype = block.get("type", "")
        if btype == "text":
            total += estimate_text_tokens(block.get("text", "") or "")
        elif btype == "image":
            total += IMAGE_TOKEN_ESTIMATE
        elif btype == "resource":
            total += estimate_text_tokens(json.dumps(block.get("resource", "")))
        else:
            total += estimate_text_tokens(json.dumps(block))
    return total


def mcp_content_needs_truncation(content: Any, max_tokens: int | None = None) -> bool:
    """Return True if the content's estimated token count exceeds the cap."""
    cap = max_tokens if max_tokens is not None else get_max_mcp_output_tokens()
    return get_content_size_estimate(content) > cap


_TRUNCATION_NOTICE = (
    "\n\n[content truncated by MCP output limit; "
    "raise MCP_MAX_OUTPUT_TOKENS to see more]"
)


def truncate_mcp_content_if_needed(
    content: Any,
    max_tokens: int | None = None,
) -> tuple[Any, bool]:
    """Truncate content to fit within the token cap.

    Returns ``(possibly_truncated_content, was_truncated)``. For plain
    strings, slices to ``cap * 4`` characters (the chars-per-token rule
    of thumb) and appends a truncation notice. For ContentBlockParam[],
    keeps blocks until the running budget would overflow, then truncates
    the tail block's text if it is a text block.
    """
    cap = max_tokens if max_tokens is not None else get_max_mcp_output_tokens()
    if not mcp_content_needs_truncation(content, cap):
        return content, False

    if isinstance(content, str):
        char_budget = max(1, cap * _CHARS_PER_TOKEN_ESTIMATE)
        return content[:char_budget] + _TRUNCATION_NOTICE, True

    if isinstance(content, list):
        kept: list[dict[str, Any]] = []
        running = 0
        for block in content:
            block_tokens = get_content_size_estimate([block])
            if running + block_tokens <= cap:
                kept.append(
                    block if isinstance(block, dict)
                    else {"type": "text", "text": str(block)}
                )
                running += block_tokens
                continue
            if isinstance(block, dict) and block.get("type") == "text":
                remaining = max(0, cap - running)
                if remaining > 0:
                    char_budget = max(1, remaining * _CHARS_PER_TOKEN_ESTIMATE)
                    text = block.get("text", "") or ""
                    kept.append({"type": "text", "text": text[:char_budget]})
            break
        kept.append({"type": "text", "text": _TRUNCATION_NOTICE.strip()})
        return kept, True

    return truncate_mcp_content_if_needed(str(content), cap)
