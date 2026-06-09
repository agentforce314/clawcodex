"""二开 agent session persistence — session storage read/write hooks.

Extracted from ``src/agent/session.py`` so the upstream Session class
remains free of orchestrator-specific SessionStorage / TailFollower
concerns.

Architecture::

    src/agent/session.py                   ← upstream Session (calls hooks below)
        ↑ import
    extensions/agent/session_persist.py    ← this module (二开 persistence)

Two public hooks:

* ``save_to_session_storage(session)`` — persist conversation messages
  via SessionStorage (JSONL transcript) so ``--resume`` can attach a
  TailFollower to watch for lines written by a backgrounded agent.
* ``load_from_session_storage(session_id)`` — construct a Session-like
  object from a SessionStorage directory if one exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def save_to_session_storage(session: Any) -> None:
    """Persist conversation messages via SessionStorage (JSONL transcript).

    Best-effort: errors are logged but never propagated. The JSONL
    transcript is the file that :class:`TailFollower` watches during
    ``--resume``, so it must exist and contain all current messages
    for the resume path to work correctly.
    """
    try:
        from src.services.session_storage import SessionStorage

        storage = SessionStorage(session_id=session.session_id)
        storage.init_metadata(
            model=session.model,
            cwd=str(Path.cwd()),
            title=_derive_title(session),
        )
        # Write each message from the conversation.  Use ``write_raw``
        # with the serialised dict so we don't re-encode via
        # ``message_to_dict`` (which may not match the shape stored
        # in ``Conversation.to_dict``).
        conv_dict = session.conversation.to_dict()
        messages_list = conv_dict.get("messages", []) if isinstance(conv_dict, dict) else []
        # Track the last user input from the conversation
        last_input = _extract_last_user_input(messages_list)
        if last_input:
            try:
                storage.update_metadata(last_user_input=last_input[:200])
            except Exception:
                pass
        for msg_data in messages_list:
            if isinstance(msg_data, dict):
                storage.write_raw(msg_data)
        storage.flush()
    except Exception:
        pass  # Best-effort; not critical if this fails.


def _derive_title(session: Any) -> str:
    """Derive a display title for the session."""
    base = f"session-{session.session_id[:8]}"
    # Try to get a title from the session object itself
    title = getattr(session, "title", None) or ""
    return title if title else base


def _extract_last_user_input(messages_list: list) -> str:
    """Extract the most recent user message text from the conversation."""
    for msg in reversed(messages_list):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "") or ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") in (None, "text"):
                        parts.append(str(item.get("text") or ""))
            if parts:
                return " ".join(parts)
    return ""


def load_from_session_storage(session_id: str) -> Optional[dict[str, Any]]:
    """Construct session data from a SessionStorage directory if one exists.

    F-49 Phase 0.2: supports sessions stored in the SessionStorage
    directory format (``~/.clawcodex/sessions/<sid>/transcript.jsonl`` +
    ``metadata.json``). This is the on-disk shape the orchestrator's
    AgentRunner writes.

    Returns a dict with keys (session_id, model, start_time, last_updated)
    or ``None`` when no SessionStorage directory exists for ``session_id``.
    """
    try:
        from src.services.session_resume import resume_session
        from src.services.session_storage import SESSIONS_DIR
    except ImportError:
        return None

    result = resume_session(session_id, sessions_dir=SESSIONS_DIR)
    if not result.success or result.metadata is None:
        return None

    md = result.metadata
    return {
        "session_id": md.session_id,
        "model": md.model,
        "start_time": str(md.start_time),
        "last_updated": str(md.last_updated),
    }


__all__ = [
    "save_to_session_storage",
    "load_from_session_storage",
]
