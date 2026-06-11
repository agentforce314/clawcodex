"""UI-neutral resumable-session listing (components C2).

Lives in services — NOT in ``src/tui`` — so the headless ``/resume``
registry command can list sessions without importing Textual (the C2
review measured 135 textual modules loaded through the screen-module
import; same dependency-direction rule the C1 review set for
``suggestions_label``). The Textual picker re-exports these names.

Standing critic condition (gap doc §5 Q2): entries with
``message_count == 0`` are FILTERED out — a headless ``/rename`` can
mint metadata-only sessions that would resume into an empty
conversation — and the hidden count is reported so the list stays
honest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ResumeEntry:
    """One selectable row: a resumable persisted session."""

    session_id: str
    title: str
    message_count: int = 0
    last_updated: float = 0.0
    model: str = ""

    def label(self) -> str:
        when = ""
        if self.last_updated:
            try:
                when = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(self.last_updated)
                )
            except Exception:
                when = ""
        parts = [self.title or self.session_id]
        meta: list[str] = []
        if when:
            meta.append(when)
        if self.message_count:
            meta.append(f"{self.message_count} msgs")
        if self.model:
            meta.append(self.model)
        if meta:
            parts.append(f"({' · '.join(meta)})")
        return "  ".join(parts)


def build_resume_entries(
    metas: Iterable[Any],
    *,
    exclude_session_id: str | None = None,
) -> tuple[list[ResumeEntry], int]:
    """Filter raw ``SessionMetadata`` rows into resumable entries.

    Returns ``(entries, hidden_count)`` where ``hidden_count`` is the
    number of metadata-only sessions suppressed (``message_count == 0``
    — the §5 Q2 decision). The active session is excluded silently (it
    is not "resumable", it is current). Duplicate session ids keep the
    first occurrence only — ``list_sessions`` orders by ``last_updated``
    descending, and a duplicated id would crash Textual's OptionList
    (DuplicateID).
    """

    entries: list[ResumeEntry] = []
    hidden = 0
    seen: set[str] = set()
    for meta in metas:
        session_id = getattr(meta, "session_id", None) or getattr(meta, "id", None)
        if not session_id:
            continue
        sid = str(session_id)
        if exclude_session_id and sid == exclude_session_id:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        count = int(getattr(meta, "message_count", 0) or 0)
        if count <= 0:
            hidden += 1
            continue
        entries.append(
            ResumeEntry(
                session_id=sid,
                title=str(getattr(meta, "title", "") or ""),
                message_count=count,
                last_updated=float(getattr(meta, "last_updated", 0.0) or 0.0),
                model=str(getattr(meta, "model", "") or ""),
            )
        )
    return entries, hidden


def filter_entries(entries: list[ResumeEntry], term: str) -> list[ResumeEntry]:
    """Case-insensitive substring filter over id + title (the TS
    argumentHint's "search term")."""

    needle = term.strip().lower()
    if not needle:
        return entries
    return [
        e
        for e in entries
        if needle in e.session_id.lower() or needle in e.title.lower()
    ]


__all__ = ["ResumeEntry", "build_resume_entries", "filter_entries"]
