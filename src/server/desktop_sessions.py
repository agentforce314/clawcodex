"""Saved-session listing + transcript hydration for ``clawcodex serve``.

The sidebar and resume both read the durable per-session files under
``<config>/sessions/<id>.json`` (written by the agent core every turn end;
transport-independent, so TUI- and desktop-created sessions appear in the
same list). Shapes here mirror the renderer's expectations
(``ui-desktop/src/types/clawcodex.ts``): ``SessionInfo`` rows for the sidebar,
``SessionMessage`` rows for transcripts.

Pure functions over the filesystem — no server state — for easy testing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Session ids are uuid-ish/token-ish path segments minted by us; anything else
# (traversal, separators) is refused before touching the filesystem.
_ID_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _safe_id(session_id: str) -> str | None:
    if session_id and set(session_id) <= _ID_OK:
        return session_id
    return None


def _read_session_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _row_from_file(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """One sidebar ``SessionInfo`` row from a saved-session file."""
    session_id = str(data.get("session_id") or path.stem)
    updated_at = data.get("updated_at")
    preview = str(data.get("preview") or "")
    name = data.get("name")
    return {
        "id": session_id,
        "title": str(name) if name else preview[:80],
        "preview": preview,
        "source": str(data.get("mode") or "desktop"),
        "started_at": updated_at,
        "last_active": updated_at,
        "message_count": int(data.get("message_count") or 0),
        "model": data.get("model"),
        "cwd": data.get("cwd"),
        "is_active": False,
    }


def list_session_rows(
    sessions_dir: Path,
    *,
    limit: int = 20,
    offset: int = 0,
    min_messages: int = 0,
) -> dict[str, Any]:
    """Paginated sidebar listing, newest-first by file mtime."""
    try:
        files = sorted(
            sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        files = []

    rows: list[dict[str, Any]] = []
    for path in files:
        data = _read_session_file(path)
        if data is None:
            continue
        row = _row_from_file(path, data)
        if row["message_count"] < min_messages:
            continue
        rows.append(row)

    window = rows[offset : offset + limit] if limit > 0 else rows[offset:]
    return {
        "sessions": window,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


def _display_kind(role: str, content: Any) -> str | None:
    """Hide plumbing rows the renderer should not paint as bubbles."""
    if role == "user" and isinstance(content, str) and content.lstrip().startswith("<system-reminder>"):
        return "hidden"
    return None


def load_session_meta(sessions_dir: Path, session_id: str) -> dict[str, Any]:
    """The stored session's model/provider pairing, for the resume spawn.

    A resumed session should come back on the model it was using — not the
    server's launch default. Empty strings when the file is absent or predates
    these fields, which callers read as "no opinion".
    """
    safe = _safe_id(session_id)
    if safe is None:
        return {"model": "", "provider": ""}
    data = _read_session_file(sessions_dir / f"{safe}.json")
    if data is None:
        return {"model": "", "provider": ""}
    model = data.get("model")
    provider = data.get("provider")
    return {
        "model": model if isinstance(model, str) else "",
        "provider": provider if isinstance(provider, str) else "",
    }


def load_session_messages(sessions_dir: Path, session_id: str) -> dict[str, Any] | None:
    """Transcript for one saved session: ``{messages, message_count, title}``.

    Messages pass through in their stored shape (string or content-block list)
    — the renderer already narrows both, live and rehydrated.
    """
    safe = _safe_id(session_id)
    if safe is None:
        return None
    data = _read_session_file(sessions_dir / f"{safe}.json")
    if data is None:
        return None
    conversation = data.get("conversation") or {}
    raw = conversation.get("messages") if isinstance(conversation, dict) else conversation
    messages: list[dict[str, Any]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "user")
        content = entry.get("content")
        message: dict[str, Any] = {"role": role, "content": content}
        kind = _display_kind(role, content)
        if kind:
            message["display_kind"] = kind
        # The wall-clock stamp and stop reason ride along when the file has
        # them: they are what lets a resumed session's Trajectory tab show the
        # run's real timings instead of "nothing recorded".
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            message["timestamp"] = timestamp
        stop_reason = entry.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason:
            message["stop_reason"] = stop_reason
        messages.append(message)
    # The stored name, so a resumed session keeps the title it was given —
    # the sidebar reads it from this same file, and a header that disagreed
    # with the row the user just clicked is its own small confusion.
    name = data.get("name")
    return {
        "messages": messages,
        "message_count": len(messages),
        "session_id": str(data.get("session_id") or safe),
        "title": str(name) if isinstance(name, str) and name.strip() else "",
    }


def _write_session_file(path: Path, data: dict[str, Any]) -> bool:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        logger.warning("desktop: could not write %s", path, exc_info=True)
        return False


def update_session_meta(sessions_dir: Path, session_id: str, **fields: Any) -> bool:
    """Patch metadata (name/archived/pinned) on a saved session file.

    Only the given keys are touched; ``name=None`` clears a custom title.
    Unknown session id or unreadable file → False.
    """
    safe = _safe_id(session_id)
    if safe is None:
        return False
    path = sessions_dir / f"{safe}.json"
    data = _read_session_file(path)
    if data is None:
        return False
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return _write_session_file(path, data)


def delete_session(sessions_dir: Path, session_id: str) -> bool:
    safe = _safe_id(session_id)
    if safe is None:
        return False
    path = sessions_dir / f"{safe}.json"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("desktop: could not delete %s", path, exc_info=True)
        return False


def search_sessions(sessions_dir: Path, query: str, *, limit: int = 40) -> dict[str, Any]:
    """Substring search over saved sessions' title/preview, newest-first."""
    needle = (query or "").strip().lower()
    listing = list_session_rows(sessions_dir, limit=1000)
    if not needle:
        return {"sessions": listing["sessions"][:limit], "query": query}
    hits = [
        row for row in listing["sessions"]
        if needle in str(row.get("title", "")).lower()
        or needle in str(row.get("preview", "")).lower()
        or needle in str(row.get("id", "")).lower()
    ]
    return {"sessions": hits[:limit], "query": query}


__all__ = [
    "delete_session",
    "list_session_rows",
    "load_session_messages",
    "search_sessions",
    "update_session_meta",
]
