"""Wire-format helpers for the OpenAI Responses API (ChatGPT Codex backend).

The ChatGPT-subscription request path (``src/auth/openai_subscription.py``)
speaks the *Responses* API — a different wire format from the Chat
Completions shape the rest of the OpenAI-compatible providers use:

- tools are FLAT ``{"type": "function", "name", ...}`` (no nested
  ``function`` object),
- the conversation is a single ``input`` item list mixing ``message``,
  ``function_call``, ``function_call_output`` and ``reasoning`` items,
- with ``store: false`` the client replays reasoning items (with
  ``encrypted_content``) itself, and item ``id``s are stripped — the
  Codex CLI convention (OpenCode does the same, transform.ts:462).

Shapes ported from OpenCode's vendored @ai-sdk/openai responses model
(``reference_projects/opencode/packages/core/src/github-copilot/responses/
convert-to-openai-responses-input.ts``) and validated live against
``https://chatgpt.com/backend-api/codex/responses`` (2026-07-12): custom
``instructions`` are accepted, id-stripped replay works, and reasoning
items are optional on replay.

Pure functions only — no I/O. The HTTP/streaming half lives in
``OpenAIProvider`` (``src/providers/openai_provider.py``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Assistant-history passthrough block carrying one raw Responses output item
# (ids stripped). Emitted via ``ChatResponse.raw_content_blocks`` — the query
# loop appends these to assistant history verbatim (query.py:1090) so the next
# subscription request can replay the exact item sequence (reasoning
# interleaving included). Foreign converters must skip this type:
# ``_convert_anthropic_messages_to_openai`` and the Anthropic provider both
# filter it; Gemini's converter drops unknown block types by construction.
RESPONSES_ITEM_BLOCK_TYPE = "openai_responses_item"

# Models served by the ChatGPT-subscription backend. Source of truth:
# OpenCode's ALLOWED_MODELS (plugin/openai/codex.ts:15) — which matches the
# live list Codex CLI caches from the backend (~/.codex/models_cache.json).
SUBSCRIPTION_MODELS = [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
]

INCLUDE_ENCRYPTED_REASONING = ["reasoning.encrypted_content"]

# Item keys never replayed: ``id`` per the Codex convention (server-assigned
# ``rs_``/``fc_``/``msg_`` ids are meaningless with ``store: false``) and
# ``status`` (response-lifecycle metadata, not content).
_STRIPPED_ITEM_KEYS = ("id", "status")


def strip_item_for_replay(item: dict[str, Any]) -> dict[str, Any]:
    """Drop server-assigned bookkeeping fields from a Responses output item."""
    return {k: v for k, v in item.items() if k not in _STRIPPED_ITEM_KEYS}


def strip_responses_item_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove ``openai_responses_item`` passthrough blocks for foreign providers.

    A mid-session ``/model`` switch away from the ChatGPT-subscription path
    leaves these blocks in assistant history; Anthropic and Chat-Completions
    APIs reject unknown content-block types, so foreign converters call this
    first. A message left with no content after filtering is dropped
    entirely (a reasoning-only turn has no foreign representation; it never
    carries tool_use blocks, so no pairing is broken).
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict)
            and block.get("type") == RESPONSES_ITEM_BLOCK_TYPE
            for block in content
        ):
            filtered = [
                block
                for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == RESPONSES_ITEM_BLOCK_TYPE
                )
            ]
            if not filtered:
                continue
            msg = {**msg, "content": filtered}
        result.append(msg)
    return result


def supports_verbosity(model: str) -> bool:
    """gpt-5.x general models accept ``text.verbosity``; codex/chat variants
    don't (OpenCode transform.ts:1189-1196)."""
    return (
        model.startswith("gpt-5")
        and "codex" not in model
        and "-chat" not in model
    )


# --- tools ---------------------------------------------------------------


def convert_tools_to_responses_format(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Anthropic tool schemas → flat Responses function-tool format.

    Same invalid-schema guards as the Chat Completions converter
    (``_convert_to_openai_tool_schema``): tools with a missing/None schema
    type are skipped, bare object schemas gain empty ``properties``.
    """
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        input_schema = tool.get("input_schema")
        if not input_schema or not isinstance(input_schema, dict):
            continue
        schema_type = input_schema.get("type")
        if schema_type is None or schema_type == "None":
            continue
        if (
            schema_type == "object"
            and "properties" not in input_schema
            and "anyOf" not in input_schema
            and "oneOf" not in input_schema
        ):
            input_schema = {**input_schema, "properties": {}}
        converted.append({
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": input_schema,
            "strict": False,
        })
    return converted


# --- input items ----------------------------------------------------------


def _system_text(content: Any) -> str:
    """Flatten a system message's content (str or text-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _image_block_to_input_image(block: dict[str, Any]) -> dict[str, Any] | None:
    """Anthropic base64 image block → Responses ``input_image`` part."""
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    data = source.get("data")
    if not data or not isinstance(data, str):
        return None
    media_type = source.get("media_type") or "image/png"
    return {
        "type": "input_image",
        "image_url": f"data:{media_type};base64,{data}",
    }


def _document_block_to_input_file(block: dict[str, Any]) -> dict[str, Any] | None:
    """Anthropic base64 document (PDF) block → Responses ``input_file`` part."""
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    data = source.get("data")
    if not data or not isinstance(data, str):
        return None
    media_type = source.get("media_type") or "application/pdf"
    return {
        "type": "input_file",
        "filename": "document.pdf",
        "file_data": f"data:{media_type};base64,{data}",
    }


def _user_block_to_part(block: Any) -> dict[str, Any] | None:
    """One user-content block → Responses input part (None = drop)."""
    if isinstance(block, str):
        return {"type": "input_text", "text": block} if block else None
    if not isinstance(block, dict):
        return None
    btype = block.get("type")
    if btype == "text":
        text = block.get("text", "")
        return {"type": "input_text", "text": str(text)} if text else None
    if btype == "image":
        return _image_block_to_input_image(block)
    if btype == "document":
        return _document_block_to_input_file(block)
    # Unknown block shapes (e.g. stale provider-specific passthroughs after a
    # mid-session model switch) are dropped — the Responses API rejects
    # unrecognised part types outright, unlike Chat Completions servers that
    # merely 400 with a pointer.
    logger.debug("Dropping unsupported user block type %r for Responses input", btype)
    return None


def _flatten_tool_result_content(
    raw_content: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """tool_result content → (text, multimodal input parts).

    Mirrors the Chat Completions converter's flattening: text blocks join by
    newline, unknown dict blocks JSON-dump, images/documents split out for a
    follow-up user message (``function_call_output.output`` is text-only).
    """
    multimodal: list[dict[str, Any]] = []
    if isinstance(raw_content, list):
        parts: list[str] = []
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict) and item.get("type") in ("image", "document"):
                translated = (
                    _image_block_to_input_image(item)
                    if item.get("type") == "image"
                    else _document_block_to_input_file(item)
                )
                if translated is not None:
                    multimodal.append(translated)
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item) if isinstance(item, dict) else str(item))
        return "\n".join(parts) if parts else "", multimodal
    if isinstance(raw_content, str):
        return raw_content, multimodal
    return str(raw_content), multimodal


def convert_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Anthropic-format messages → Responses ``input`` items + instructions.

    Returns ``(input_items, instructions)`` where ``instructions`` is the
    flattened text of every ``role=system`` message (the query loop injects
    the system prompt as a leading system message for non-Anthropic
    providers, query.py:875). It rides the top-level ``instructions`` field —
    Codex-CLI parity; the backend accepts arbitrary instructions (validated
    live).

    Assistant messages that carry ``openai_responses_item`` passthrough
    blocks replay those raw items verbatim in order and skip the projected
    text/tool_use blocks (which are projections of the same items — see
    ``OpenAIProvider`` response assembly). Anthropic-native assistant
    messages (API-key sessions, model switches) are reconstructed from their
    text/tool_use blocks instead.
    """
    input_items: list[dict[str, Any]] = []
    instructions_parts: list[str] = []

    # Orphan guard, ported from ``_convert_anthropic_messages_to_openai``:
    # a ``function_call_output`` whose call_id was never sent as a
    # ``function_call`` gets the request rejected. Collect known ids from
    # both projections and passthrough items.
    known_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                known_call_ids.add(str(block.get("id", "")))
            elif block.get("type") == RESPONSES_ITEM_BLOCK_TYPE:
                item = block.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    known_call_ids.add(str(item.get("call_id", "")))

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            text = _system_text(content)
            if text:
                instructions_parts.append(text)
            continue

        if role == "user":
            if isinstance(content, str):
                if content:
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    })
                continue
            if not isinstance(content, list):
                continue

            user_parts: list[dict[str, Any]] = []
            deferred_multimodal: list[dict[str, Any]] = []
            # Tool outputs are emitted before the remaining user content,
            # preserving the Chat Completions converter's ordering policy
            # (outputs bind to the immediately preceding function_call turn).
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    call_id = str(block.get("tool_use_id", ""))
                    if call_id not in known_call_ids:
                        logger.debug(
                            "Dropping orphan tool_result for call_id=%r "
                            "(no matching function_call in history)",
                            call_id,
                        )
                        continue
                    flat, multimodal = _flatten_tool_result_content(
                        block.get("content", "")
                    )
                    if multimodal:
                        correlation = (
                            f"[multimodal content for tool_use_id={call_id} "
                            "delivered in the following message]"
                        )
                        flat = f"{flat}\n\n{correlation}" if flat else correlation
                        deferred_multimodal.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"[content for tool_use_id={call_id}]",
                                },
                                *multimodal,
                            ],
                        })
                    if not flat:
                        flat = "[empty tool result]"
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": flat,
                    })
                else:
                    part = _user_block_to_part(block)
                    if part is not None:
                        user_parts.append(part)

            input_items.extend(deferred_multimodal)
            if user_parts:
                input_items.append({"role": "user", "content": user_parts})
            continue

        if role == "assistant":
            if isinstance(content, str):
                if content:
                    input_items.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    })
                continue
            if not isinstance(content, list):
                continue

            passthrough_items = [
                block["item"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == RESPONSES_ITEM_BLOCK_TYPE
                and isinstance(block.get("item"), dict)
            ]
            if passthrough_items:
                # Raw items carry the full generated sequence (reasoning /
                # message / function_call, in order); text and tool_use
                # blocks in the same message are projections of these items
                # and must not be double-sent.
                input_items.extend(
                    strip_item_for_replay(item) for item in passthrough_items
                )
                continue

            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(str(text))
                elif btype == "tool_use":
                    tool_input = block.get("input", {})
                    tool_calls.append({
                        "type": "function_call",
                        "call_id": str(block.get("id", "")),
                        "name": str(block.get("name", "")),
                        "arguments": (
                            json.dumps(tool_input)
                            if isinstance(tool_input, dict)
                            else str(tool_input)
                        ),
                    })
                # Other block types (thinking from an Anthropic turn, advisor
                # passthroughs, …) have no Responses representation — drop.
            if text_parts:
                input_items.append({
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "\n".join(text_parts)}
                    ],
                })
            input_items.extend(tool_calls)
            continue

        # Unknown role — drop (mirrors the tolerance of the CC converter's
        # fallthrough, which the Responses API does not share).
        logger.debug("Dropping message with unsupported role %r", role)

    return input_items, "\n\n".join(instructions_parts)


# --- responses --------------------------------------------------------------


def build_usage_dict(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Responses usage JSON → the ChatResponse usage shape.

    ``billing_mode: subscription`` zeroes the cost in ``record_api_usage``
    (cost_tracker.py — the #697 mechanism); token counts still feed the
    context-left display.
    """
    usage = usage or {}
    input_details = usage.get("input_tokens_details") or {}
    result: dict[str, Any] = {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "billing_mode": "subscription",
    }
    cached = input_details.get("cached_tokens")
    if cached:
        result["cache_read_input_tokens"] = int(cached)
    return result


def parse_sse_line(line: str) -> dict[str, Any] | None:
    """One SSE line → event dict (None for non-data/keepalive/DONE lines)."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        event = json.loads(data)
    except (ValueError, json.JSONDecodeError):
        return None
    return event if isinstance(event, dict) else None
