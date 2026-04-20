"""
Async memory pre-fetch — aligned with typescript/src/memdir/findRelevantMemories.ts.

Scans memory file headers and selects the most relevant ones (up to 5)
for progressive disclosure during conversation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_RELEVANT_MEMORIES = 5


@dataclass
class RelevantMemory:
    """A memory file selected as relevant to the current query."""

    path: str
    mtime_ms: float


@dataclass
class MemoryHeader:
    """Parsed header from a memory file."""

    filename: str
    file_path: str
    description: str
    mtime_ms: float


# ---------------------------------------------------------------------------
# Memory header scanning
# ---------------------------------------------------------------------------

def _parse_memory_header(file_path: str) -> MemoryHeader | None:
    """
    Read just the header/first few lines of a memory file.

    Extracts a description from the first non-empty, non-heading line
    or from YAML frontmatter 'description' field.
    """
    try:
        p = Path(file_path)
        if not p.is_file():
            return None
        mtime_ms = p.stat().st_mtime * 1000

        content = p.read_text(encoding="utf-8")
        if not content.strip():
            return None

        # Try frontmatter description
        description = ""
        lines = content.splitlines()
        if lines and lines[0].strip() == "---":
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "---":
                    break
                if line.strip().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break

        # Fallback: first meaningful line
        if not description:
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    description = stripped.lstrip("#").strip()
                    break
                if stripped != "---":
                    description = stripped[:200]
                    break

        return MemoryHeader(
            filename=p.name,
            file_path=file_path,
            description=description or p.name,
            mtime_ms=mtime_ms,
        )
    except Exception:
        return None


async def scan_memory_files(
    memory_dir: str,
) -> list[MemoryHeader]:
    """
    Scan a directory for memory files and parse their headers.

    Mirrors TS scanMemoryFiles from memoryScan.ts.
    Excludes MEMORY.md (already in system prompt).
    """
    dir_path = Path(memory_dir)
    if not dir_path.is_dir():
        return []

    headers: list[MemoryHeader] = []
    try:
        for entry in sorted(dir_path.iterdir()):
            if not entry.is_file():
                continue
            if entry.name == "MEMORY.md":
                continue
            if entry.suffix.lower() not in (".md", ".txt"):
                continue
            header = _parse_memory_header(str(entry))
            if header:
                headers.append(header)
    except PermissionError:
        pass
    except Exception:
        logger.debug("Error scanning memory dir %s", memory_dir, exc_info=True)

    return headers


def format_memory_manifest(headers: list[MemoryHeader]) -> str:
    """Format memory headers into a manifest string for LLM selection."""
    lines: list[str] = []
    for h in headers:
        lines.append(f"- {h.filename}: {h.description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Relevance selection — simplified version of TS findRelevantMemories
# ---------------------------------------------------------------------------

async def find_relevant_memories(
    query: str,
    memory_dir: str,
    already_surfaced: set[str] | None = None,
    provider: Any = None,
) -> list[RelevantMemory]:
    """
    Find memory files relevant to a query.

    Mirrors TS findRelevantMemories from findRelevantMemories.ts.

    In the TS implementation, this uses a side query to Sonnet to select
    relevant memories. In our Python implementation, we support two modes:

    1. If a provider is given, use it to select memories via LLM.
    2. Otherwise, use keyword-based heuristic matching.

    Returns list of RelevantMemory (up to MAX_RELEVANT_MEMORIES).
    """
    surfaced = already_surfaced or set()
    headers = await scan_memory_files(memory_dir)
    headers = [h for h in headers if h.file_path not in surfaced]

    if not headers:
        return []

    if provider is not None:
        return await _select_with_provider(query, headers, provider)

    return _select_with_heuristic(query, headers)


async def _select_with_provider(
    query: str,
    headers: list[MemoryHeader],
    provider: Any,
) -> list[RelevantMemory]:
    """Use an LLM provider to select relevant memories."""
    import json

    manifest = format_memory_manifest(headers)
    system_prompt = (
        "You are selecting memories that will be useful to Claude Code as it "
        "processes a user's query. You will be given the user's query and a list "
        "of available memory files with their filenames and descriptions.\n\n"
        "Return a JSON object with a single key 'selected_memories' containing "
        "a list of filenames for the memories that will clearly be useful "
        f"(up to {MAX_RELEVANT_MEMORIES}). Only include memories that you are certain "
        "will be helpful. If unsure, return an empty list."
    )

    messages = [
        {"role": "user", "content": f"Query: {query}\n\nAvailable memories:\n{manifest}"},
    ]

    try:
        response = provider.chat(messages, system=system_prompt, max_tokens=256)
        if response and response.content:
            parsed = json.loads(response.content)
            selected_names = parsed.get("selected_memories", [])
            by_filename = {h.filename: h for h in headers}
            result: list[RelevantMemory] = []
            for name in selected_names[:MAX_RELEVANT_MEMORIES]:
                header = by_filename.get(name)
                if header:
                    result.append(RelevantMemory(
                        path=header.file_path,
                        mtime_ms=header.mtime_ms,
                    ))
            return result
    except Exception:
        logger.debug("LLM memory selection failed, falling back to heuristic", exc_info=True)

    return _select_with_heuristic(query, headers)


def _select_with_heuristic(
    query: str,
    headers: list[MemoryHeader],
) -> list[RelevantMemory]:
    """Simple keyword-matching heuristic for memory selection."""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored: list[tuple[float, MemoryHeader]] = []
    for header in headers:
        desc_lower = header.description.lower()
        name_lower = header.filename.lower()
        combined = f"{name_lower} {desc_lower}"

        score = 0.0
        for word in query_words:
            if len(word) < 3:
                continue
            if word in combined:
                score += 1.0

        if score > 0:
            scored.append((score, header))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        RelevantMemory(path=h.file_path, mtime_ms=h.mtime_ms)
        for _, h in scored[:MAX_RELEVANT_MEMORIES]
    ]
