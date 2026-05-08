"""Backward-compatible re-export shim — superseded by ``src.memdir``.

Per the ch11 refactor plan (Slice B3), the recall pipeline moved into
``src/memdir/`` to live next to the rest of the auto-memory subsystem.
This module is preserved for one release as a thin shim so existing
imports keep working; new code should import from ``src.memdir``
directly.

Notable behavior change worth flagging to consumers: the original
implementation accepted ``provider=None`` and fell back to a keyword
heuristic. The chapter explicitly rejects that (`Use an LLM for recall,
not keywords or embeddings`), and the new pipeline returns an empty list
on missing/failing provider rather than retrieving with keyword
matching. Callers that previously relied on the heuristic will see no
results until a real provider is wired in.
"""

from __future__ import annotations

import logging
from collections.abc import Set as _AbstractSet
from typing import Any

from src.memdir.find_relevant_memories import (
    MAX_RELEVANT_MEMORIES,
    RelevantMemory,
)
from src.memdir.find_relevant_memories import (
    find_relevant_memories as _find_relevant_memories_new,
)
from src.memdir.memory_scan import (
    MemoryHeader,
    format_memory_manifest,
    scan_memory_files,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_RELEVANT_MEMORIES",
    "MemoryHeader",
    "RelevantMemory",
    "find_relevant_memories",
    "format_memory_manifest",
    "scan_memory_files",
]

_DEPRECATION_LOGGED = False


def _log_deprecation_once() -> None:
    global _DEPRECATION_LOGGED
    if _DEPRECATION_LOGGED:
        return
    _DEPRECATION_LOGGED = True
    logger.debug(
        "src.context_system.memory_prefetch is deprecated; "
        "import from src.memdir instead."
    )


async def find_relevant_memories(
    query: str,
    memory_dir: str,
    already_surfaced: _AbstractSet[str] | None = None,
    provider: Any = None,
    *,
    cancel_event: Any = None,
    recent_tools: Any = (),
) -> list[RelevantMemory]:
    """Compatibility wrapper around :func:`src.memdir.find_relevant_memories`.

    The new pipeline requires both a ``provider`` and a ``cancel_event``
    (kw-only). For callers that haven't migrated yet, this shim
    synthesizes an empty ``cancel_event`` and refuses to run without a
    provider — the keyword fallback is intentionally gone.
    """
    _log_deprecation_once()
    if provider is None:
        return []
    import asyncio

    event = cancel_event if cancel_event is not None else asyncio.Event()
    return await _find_relevant_memories_new(
        query,
        memory_dir,
        cancel_event=event,
        provider=provider,
        recent_tools=recent_tools,
        already_surfaced=already_surfaced or frozenset(),
    )
