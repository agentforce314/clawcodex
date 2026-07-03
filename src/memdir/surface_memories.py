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
# R5 round-5 (ch11 #4) — total cap across ALL files surfaced in one turn.
# find_relevant_memories selects up to 5, each capped at 4 KB, so a single
# turn could inject ~20 KB of memory bodies; TS bounds the aggregate. Cap
# the combined reminder so one turn can't bloat the request.
MAX_TOTAL_SURFACE_CHARS = 12288  # 12 KB (≈3× the per-file cap)


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


def build_relevant_memory_reminder_with_paths(
    memories: list[Any],
) -> tuple[str | None, list[str]]:
    """Wrap the surfaced memory bodies in a single ``<system-reminder>``.

    ``memories`` is a list of ``RelevantMemory`` (``.path``). Returns
    ``(reminder, surfaced_paths)`` — ``reminder`` is None when nothing
    readable was surfaced, and ``surfaced_paths`` is EXACTLY the files that
    made it into the reminder (fewer than ``memories`` when the R5 #4
    aggregate cap trims the tail). The caller de-dups on ``surfaced_paths``,
    NOT the full selection, so a capped-out memory can still surface later."""
    blocks: list[str] = []
    surfaced: list[str] = []
    total = 0
    for mem in memories:
        path = getattr(mem, "path", None) or (
            mem.get("path") if isinstance(mem, dict) else None
        )
        if not path:
            continue
        body = _read_capped(str(path))
        if not body:
            continue
        block = f"### {path}\n{body}"
        # R5 (ch11 #4) — stop once the aggregate turn cap is reached rather
        # than injecting every selected file unbounded.
        if total + len(block) > MAX_TOTAL_SURFACE_CHARS and blocks:
            blocks.append(
                "… (additional relevant memories omitted; Read the memory "
                "directory for more)"
            )
            break
        blocks.append(block)
        surfaced.append(str(path))
        total += len(block)
    if not blocks:
        return None, []
    joined = "\n\n".join(blocks)
    reminder = (
        "<system-reminder>\n"
        "The following saved memories were selected as relevant to the "
        "current request (recalled automatically from your memory "
        "directory). Treat them as background context — they reflect what "
        "was true when written and may be stale; verify before relying on "
        "specifics.\n\n"
        f"{joined}\n"
        "</system-reminder>"
    )
    return reminder, surfaced


def build_relevant_memory_reminder(memories: list[Any]) -> str | None:
    """Back-compat wrapper: the reminder string only (drops the
    surfaced-paths list). Prefer ``build_relevant_memory_reminder_with_paths``
    so de-dup tracks exactly what was surfaced."""
    reminder, _ = build_relevant_memory_reminder_with_paths(memories)
    return reminder


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
    reminder, surfaced_paths = build_relevant_memory_reminder_with_paths(memories)
    if reminder is None:
        return None
    # R5 (ch11 #4) — de-dup ONLY the paths that actually made it into the
    # reminder; a memory trimmed by the aggregate cap stays eligible.
    for path in surfaced_paths:
        already_surfaced.add(path)
    return reminder
