"""ch11 round-4 WI-1 — surface query-relevant memory bodies.

The read-side companion to ``find_relevant_memories`` (the LLM selector).
Port of TS ``readMemoriesForSurfacing`` + ``getRelevantMemoryAttachments``
(``utils/attachments.ts:2197-2242``): read each selected memory file,
cap it (200 lines / 4 KB with a truncation note), and wrap the set in a
single ``<system-reminder>`` so the model sees the actual memory content
relevant to the current turn — not just the MEMORY.md index pointer.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Per-file surfacing caps (TS readMemoriesForSurfacing: 200 lines / 4 KB).
MAX_SURFACE_LINES = 200
MAX_SURFACE_CHARS = 4096


def _read_capped(path: str) -> str | None:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a missing/unreadable memory is skipped
        return None
    lines = raw.splitlines()
    truncated = False
    if len(lines) > MAX_SURFACE_LINES:
        lines = lines[:MAX_SURFACE_LINES]
        truncated = True
    body = "\n".join(lines)
    if len(body) > MAX_SURFACE_CHARS:
        body = body[:MAX_SURFACE_CHARS]
        truncated = True
    if truncated:
        body += "\n… (truncated; Read the file for the full memory)"
    return body


def build_relevant_memory_reminder(memories: list[Any]) -> str | None:
    """Wrap the surfaced memory bodies in a single ``<system-reminder>``.

    ``memories`` is a list of ``RelevantMemory`` (``.path``). Returns None
    when nothing readable was surfaced."""
    blocks: list[str] = []
    for mem in memories:
        path = getattr(mem, "path", None) or (
            mem.get("path") if isinstance(mem, dict) else None
        )
        if not path:
            continue
        body = _read_capped(str(path))
        if not body:
            continue
        blocks.append(f"### {path}\n{body}")
    if not blocks:
        return None
    joined = "\n\n".join(blocks)
    return (
        "<system-reminder>\n"
        "The following saved memories were selected as relevant to the "
        "current request (recalled automatically from your memory "
        "directory). Treat them as background context — they reflect what "
        "was true when written and may be stale; verify before relying on "
        "specifics.\n\n"
        f"{joined}\n"
        "</system-reminder>"
    )


async def get_relevant_memory_reminder(
    query_text: str,
    memory_dir: str,
    *,
    provider: Any,
    already_surfaced: set[str],
    recent_tools: tuple[str, ...] = (),
    cancel_event: asyncio.Event | None = None,
) -> str | None:
    """Run the LLM recall for ``query_text`` and return a
    ``<system-reminder>`` string with the relevant memory bodies, or None.

    Never raises. Updates ``already_surfaced`` in place with the paths it
    surfaces so a session doesn't re-inject the same memory every turn.
    """
    if not query_text or not query_text.strip() or provider is None:
        return None
    try:
        from src.memdir.find_relevant_memories import find_relevant_memories

        ev = cancel_event or asyncio.Event()
        memories = await find_relevant_memories(
            query_text, memory_dir,
            cancel_event=ev, provider=provider,
            recent_tools=recent_tools,
            already_surfaced=frozenset(already_surfaced),
        )
    except Exception:  # noqa: BLE001 — recall failure must never block a turn
        logger.debug("memory recall failed", exc_info=True)
        return None
    if not memories:
        return None
    reminder = build_relevant_memory_reminder(memories)
    if reminder is None:
        return None
    for mem in memories:
        path = getattr(mem, "path", None)
        if path:
            already_surfaced.add(str(path))
    return reminder
