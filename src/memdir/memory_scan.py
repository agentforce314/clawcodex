"""Memory directory scanning primitives.

Ports `typescript/src/memdir/memoryScan.ts`. The scan reads only the
first ``FRONTMATTER_MAX_LINES`` of each file (frontmatter only), enforces
a depth cap (DoS guard against symlinked/deep trees), caps the result at
the newest ``MAX_MEMORY_FILES`` files, and excludes ``MEMORY.md``
itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from .memdir import ENTRYPOINT_NAME
from .memory_types import MemoryType, parse_memory_type

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryHeader",
    "MAX_MEMORY_FILES",
    "FRONTMATTER_MAX_LINES",
    "MAX_DEPTH",
    "scan_memory_files",
    "format_memory_manifest",
]

MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30
MAX_DEPTH = 3


@dataclass(frozen=True)
class MemoryHeader:
    """A scanned memory file's header — frontmatter + mtime."""

    filename: str  # relative path within the memory dir
    file_path: str  # absolute path
    mtime_ms: float
    description: str | None
    type: MemoryType | None


def _read_first_lines(path: Path, n: int) -> str:
    """Read up to *n* lines from *path* without buffering the rest."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return "".join(islice(f, n))
    except OSError:
        return ""


def _parse_memory_frontmatter(text: str) -> dict[str, str]:
    """Minimal frontmatter parser for memory files.

    Memory files declare only top-level scalars (``name``, ``description``,
    ``type``) per the chapter's frontmatter contract, so a real YAML parser
    is overkill. This parser is tolerant: missing fences, extra whitespace,
    quoted values are all handled. Returns an empty dict when no
    frontmatter block is present.

    Memory-specific (not a generic YAML port) so that the recall pipeline
    has no PyYAML runtime dependency — keeps Slice B self-contained.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _depth(rel_parts: tuple[str, ...]) -> int:
    """Directory depth of a relative path (file at top level == 0)."""
    return max(0, len(rel_parts) - 1)


async def scan_memory_files(
    memory_dir: str,
    cancel_event: asyncio.Event | None = None,
) -> list[MemoryHeader]:
    """Scan *memory_dir* recursively for ``.md`` memory files.

    Excludes ``MEMORY.md``. Depth-capped at :data:`MAX_DEPTH` to prevent
    DoS via deep / symlink-looping directory trees. Reads only the first
    :data:`FRONTMATTER_MAX_LINES` of each file. Returns the newest
    :data:`MAX_MEMORY_FILES` files by mtime.
    """
    base = Path(memory_dir)
    if not base.is_dir():
        return []

    headers: list[MemoryHeader] = []

    def _walk() -> list[tuple[Path, tuple[str, ...]]]:
        # Collect candidate (path, rel_parts) pairs synchronously — the
        # work is small and saves us juggling async filesystem stubs.
        out: list[tuple[Path, tuple[str, ...]]] = []
        try:
            for path in base.rglob("*.md"):
                rel = path.relative_to(base)
                parts = rel.parts
                if parts[-1] == ENTRYPOINT_NAME:
                    continue
                if _depth(parts) > MAX_DEPTH:
                    continue
                out.append((path, parts))
        except OSError:
            return []
        return out

    candidates = _walk()
    for path, parts in candidates:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("memory scan cancelled")
        try:
            stat = path.stat()
        except OSError:
            continue
        head = _read_first_lines(path, FRONTMATTER_MAX_LINES)
        fm = _parse_memory_frontmatter(head)
        description = fm.get("description") or None
        headers.append(
            MemoryHeader(
                filename="/".join(parts),
                file_path=str(path),
                mtime_ms=stat.st_mtime * 1000.0,
                description=description,
                type=parse_memory_type(fm.get("type")),
            )
        )

    headers.sort(key=lambda h: h.mtime_ms, reverse=True)
    return headers[:MAX_MEMORY_FILES]


def format_memory_manifest(headers: list[MemoryHeader]) -> str:
    """One-line-per-file manifest used by both recall and extraction.

    Format mirrors TS ``formatMemoryManifest``:
    ``- [type] filename (ISO-timestamp): description``
    The ``[type]`` tag and trailing description are conditional.
    """
    lines: list[str] = []
    for h in headers:
        tag = f"[{h.type}] " if h.type else ""
        ts = (
            datetime.fromtimestamp(h.mtime_ms / 1000.0, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if h.description:
            lines.append(f"- {tag}{h.filename} ({ts}): {h.description}")
        else:
            lines.append(f"- {tag}{h.filename} ({ts})")
    return "\n".join(lines)
