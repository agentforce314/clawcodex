"""Session extension — resume_with_tail.

Provides ``resume_session_with_tail`` which complements ``Session.resume``
with background-agent awareness and a :class:`TailFollower` for watching
transcript lines written by a backgrounded agent.

This was extracted from ``src/agent/session.py`` to keep the upstream
session module clean of 二开-specific concerns.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.agent.session import Session

logger = logging.getLogger(__name__)


def resume_session_with_tail(
    session_id: str,
) -> tuple[Optional["Session"], Any | None]:
    """Resume a session and optionally attach a TailFollower.

    Returns ``(session, tail_follower)`` where *session* is the
    reconstructed :class:`Session` (or ``None`` if not found) and
    *tail_follower* is a :class:`~src.services.tail_follower.TailFollower`
    started from the current transcript end (so only *new* lines
    written by the backgrounded agent are yielded), or ``None``
    if the transcript file does not exist or the import fails.
    """
    session = Session.resume(session_id)
    if session is None:
        return None, None

    # Check for a running background agent
    try:
        from src.agent.background_runner import get_background_runner_status

        bg_status = get_background_runner_status(session_id)
        logger.info(
            "resume_session_with_tail: session=%s, bg_status=%s",
            session_id,
            bg_status,
        )
    except Exception:
        pass

    tail_follower = None
    try:
        from src.services.tail_follower import TailFollower
        from src.services.session_storage import SessionStorage

        storage = SessionStorage(session_id=session_id)
        transcript_path = storage._transcript_path
        if transcript_path.exists():
            current_size = transcript_path.stat().st_size
            tail_follower = TailFollower(str(transcript_path))
            # ``start()`` is async but we only need a synchronous
            # record of the offset.  The actual async iteration
            # happens in AgentBridge's worker thread.
            tail_follower._offset = current_size
    except Exception:
        tail_follower = None

    return session, tail_follower


__all__ = [
    "resume_session_with_tail",
]
