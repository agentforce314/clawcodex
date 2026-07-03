"""LLM-driven recall of relevant memories.

Ports `typescript/src/memdir/findRelevantMemories.ts`. Replaces the
keyword-heuristic in ``src/context_system/memory_prefetch.py`` with the
chapter's named pipeline:

    scan → filter already-surfaced → manifest → side-query →
    validate filenames → return RelevantMemory list

The keyword fallback is intentionally removed. The chapter rejects it
("embedding similarity / keyword matching cannot express 'do not select
memories for tools already in active use'"). On provider failure, return
an empty list.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from .memory_scan import MemoryHeader, format_memory_manifest, scan_memory_files

logger = logging.getLogger(__name__)


def _resolve_recall_model(provider: Any) -> str | None:
    """R5 round-5 (ch11 #2) — the model for the memory-selector side-query.

    TS pins the selector to a small default (``getDefaultSonnetModel``) so a
    turn on an expensive session model doesn't pay full price for the recall
    call. The multi-provider port wires the ``small_fast_model`` setting —
    BUT its shipped default is an Anthropic id (``claude-3-5-haiku-…``,
    settings/constants.py), which is only valid on the first-party Anthropic
    endpoint. Passing it to a DeepSeek/OpenAI/Minimax session would 400 and
    (since recall swallows errors) silently kill recall every turn (critic
    M1). So the pin applies ONLY when the SESSION provider is an
    ``AnthropicProvider``; every other provider (incl. Minimax, which runs
    the Anthropic SDK against a different endpoint) falls back to the session
    model. Returns None to signal that fallback. Never raises.

    Note: ``AnthropicProvider`` also covers a custom ``ANTHROPIC_BASE_URL`` /
    Bedrock-shim / proxy endpoint that might itself reject the Haiku id — in
    that case the selector 400s and recall safe-degrades to no-recall (same
    swallow-to-None path), narrower than the cross-provider M1. The real fix
    for both is a future ``small_fast_model_provider`` pairing (mirroring
    ``advisor_model``/``advisor_provider``); deferred."""
    try:
        from src.providers.anthropic_provider import AnthropicProvider

        # Minimax subclasses BaseProvider (not AnthropicProvider) and targets
        # its own endpoint, so isinstance correctly excludes it.
        if not isinstance(provider, AnthropicProvider):
            return None
        from src.settings.settings import get_settings

        model = (getattr(get_settings(), "small_fast_model", "") or "").strip()
        return model or None
    except Exception:  # noqa: BLE001 — a settings/import failure must not block recall
        return None


__all__ = [
    "RelevantMemory",
    "MAX_RELEVANT_MEMORIES",
    "find_relevant_memories",
]

MAX_RELEVANT_MEMORIES = 5

_SELECT_SYSTEM_PROMPT = (
    "You are selecting memories that will be useful to Claude Code as it "
    "processes a user's query. You will be given the user's query and a list "
    "of available memory files with their filenames and descriptions.\n\n"
    f"Return a list of filenames for the memories that will clearly be "
    f"useful to Claude Code as it processes the user's query (up to "
    f"{MAX_RELEVANT_MEMORIES}). Only include memories that you are certain "
    "will be helpful based on their name and description.\n"
    "- If you are unsure if a memory will be useful in processing the user's "
    "query, then do not include it in your list. Be selective and "
    "discerning.\n"
    "- If there are no memories in the list that would clearly be useful, "
    "feel free to return an empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories "
    "that are usage reference or API documentation for those tools (Claude "
    "Code is already exercising them). DO still select memories containing "
    "warnings, gotchas, or known issues about those tools — active use is "
    "exactly when those matter.\n"
)

_SELECTOR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_memories": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_RELEVANT_MEMORIES,
        },
    },
    "required": ["selected_memories"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RelevantMemory:
    """A memory file that the selector chose for the current turn."""

    path: str
    mtime_ms: float


async def _select_with_provider(
    query: str,
    memories: list[MemoryHeader],
    *,
    provider: Any,
    recent_tools: Sequence[str],
    cancel_event: asyncio.Event,
) -> list[str]:
    """Issue the side-query and return validated filenames.

    Returns an empty list on any provider error or invalid response —
    no fallback, no retries. The chapter explicitly forbids a keyword
    fallback.
    """
    valid = {h.filename for h in memories}
    manifest = format_memory_manifest(memories)
    tools_section = (
        f"\n\nRecently used tools: {', '.join(recent_tools)}"
        if recent_tools
        else ""
    )
    user_msg = (
        f"Query: {query}\n\nAvailable memories:\n{manifest}{tools_section}"
    )
    messages = [{"role": "user", "content": user_msg}]

    if cancel_event.is_set():
        return []

    if not hasattr(provider, "chat_async"):
        # Sync provider in an async function would block the loop. The
        # selector is a side query — degrade to no-result rather than
        # hang the main turn.
        logger.debug(
            "memdir selector: provider lacks chat_async; skipping selection"
        )
        return []
    # R5 (ch11 #2) — run the selector on the cheap small_fast_model when
    # configured, so recall doesn't pay the (Opus/DeepSeek) session-model
    # price every turn. Omit `model` entirely when unset → session model.
    recall_kwargs: dict[str, Any] = {
        "system": _SELECT_SYSTEM_PROMPT,
        "max_tokens": 256,
        "output_format": {
            "type": "json_schema",
            "schema": _SELECTOR_JSON_SCHEMA,
        },
    }
    recall_model = _resolve_recall_model(provider)
    if recall_model:
        recall_kwargs["model"] = recall_model
    try:
        response = await provider.chat_async(messages, **recall_kwargs)
    except Exception as exc:
        logger.debug("memdir selector failed: %s", exc)
        return []

    if response is None:
        return []
    content = getattr(response, "content", None)
    if not content:
        return []
    if isinstance(content, list):
        # Anthropic-shape: list of content blocks; concatenate text blocks.
        text = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    else:
        text = str(content)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        logger.debug("memdir selector returned non-JSON: %r", text[:200])
        return []
    selected = parsed.get("selected_memories") if isinstance(parsed, dict) else None
    if not isinstance(selected, list):
        return []
    # Validate filenames against the known set; drop hallucinated names.
    # Defense in depth: even though the prompt says "up to 5" and the
    # JSON schema sets maxItems: 5, enforce the cap here too in case the
    # provider abstraction silently drops the schema.
    validated = [
        name for name in selected if isinstance(name, str) and name in valid
    ]
    return validated[:MAX_RELEVANT_MEMORIES]


async def find_relevant_memories(
    query: str,
    memory_dir: str,
    *,
    cancel_event: asyncio.Event,
    provider: Any,
    recent_tools: Sequence[str] = (),
    already_surfaced: AbstractSet[str] = frozenset(),
) -> list[RelevantMemory]:
    """Find memory files relevant to *query* via LLM selection.

    Required arguments are keyword-only because the gap analysis named
    "no abort signal" and "no provider validation" as current bugs;
    making them required prevents callers from accidentally falling
    back to a degenerate path.
    """
    headers = await scan_memory_files(memory_dir, cancel_event)
    if not headers:
        return []
    headers = [h for h in headers if h.file_path not in already_surfaced]
    if not headers:
        return []

    selected_filenames = await _select_with_provider(
        query,
        headers,
        provider=provider,
        recent_tools=tuple(recent_tools),
        cancel_event=cancel_event,
    )
    by_filename = {h.filename: h for h in headers}
    result: list[RelevantMemory] = []
    for name in selected_filenames:
        header = by_filename.get(name)
        if header is None:
            continue
        result.append(
            RelevantMemory(path=header.file_path, mtime_ms=header.mtime_ms)
        )

    logger.debug(
        "memdir recall: scanned=%d selected=%d", len(headers), len(result)
    )
    return result
