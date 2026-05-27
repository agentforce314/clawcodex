"""Session management with persistence.

The session ID is authoritative-from-bootstrap: ``Session.create`` reads
``get_session_id()`` rather than generating its own. This fixes the
strftime-collision bug (sessions started in the same second would have
overlapped IDs) and unifies session identity across the codebase — the
bootstrap singleton is the single source of truth, exactly per Chapter 3.

``Session.load(sid)`` continues to read from disk by ID; the resume path
should call ``switch_session(SessionId(sid))`` first (or via a wrapping
helper) to update the bootstrap singleton, then call ``Session.load(sid)``
to reconstruct the per-conversation Persistence record.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from src.bootstrap.state import (
    get_model_usage,
    get_session_id,
    get_start_time,
    get_total_api_duration,
    get_total_api_duration_without_retries,
    get_total_cost_usd,
    get_total_lines_added,
    get_total_lines_removed,
    get_total_tool_duration,
)

from .conversation import Conversation

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """Session manager with persistence."""
    session_id: str
    provider: str
    model: str
    conversation: Conversation = field(default_factory=Conversation)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def save(self):
        """Save session to disk including a cost block.

        Ch03 round-2 (R2.1): the ``cost`` key matches the schema read by
        ``src/services/cost_restore.py:restore_cost_state_for_session``
        so a save → load round-trip restores bootstrap counters
        (`total_cost_usd`, durations, lines added/removed, per-model
        usage). Previously this method emitted no cost block; the
        restore reader hit defaults of 0 unconditionally.

        Also persists conversation messages via :class:`SessionStorage`
        (JSONL transcript) so ``--resume`` can attach a
        :class:`TailFollower` to watch for lines written by a
        backgrounded agent.
        """
        session_dir = Path.home() / ".clawcodex" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)

        session_file = session_dir / f"{self.session_id}.json"

        cost_block = _snapshot_cost_block()

        session_data = {
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "conversation": self.conversation.to_dict(),
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(),
            "cost": cost_block,
        }

        with open(session_file, 'w') as f:
            json.dump(session_data, f, indent=2)

        self.updated_at = datetime.now().isoformat()

        # Also persist via SessionStorage (JSONL transcript) so
        # TailFollower can observe new lines during --resume.
        self._save_to_session_storage()

    def _save_to_session_storage(self) -> None:
        """Persist conversation messages via SessionStorage (JSONL transcript).

        Best-effort: errors are logged but never propagated. The JSONL
        transcript is the file that :class:`TailFollower` watches during
        ``--resume``, so it must exist and contain all current messages
        for the resume path to work correctly.
        """
        try:
            from src.services.session_storage import SessionStorage

            storage = SessionStorage(session_id=self.session_id)
            storage.init_metadata(
                model=self.model,
                cwd=str(Path.cwd()),
                title=f"session-{self.session_id[:8]}",
            )
            # Write each message from the conversation.  Use ``write_raw``
            # with the serialised dict so we don't re-encode via
            # ``message_to_dict`` (which may not match the shape stored
            # in ``Conversation.to_dict``).
            conv_dict = self.conversation.to_dict()
            messages_list = conv_dict.get("messages", []) if isinstance(conv_dict, dict) else []
            for msg_data in messages_list:
                if isinstance(msg_data, dict):
                    storage.write_raw(msg_data)
            storage.flush()
        except Exception:
            pass  # Best-effort; not critical if this fails.

    @classmethod
    def load(cls, session_id: str) -> Optional['Session']:
        """Load session from disk."""
        session_file = Path.home() / ".clawcodex" / "sessions" / f"{session_id}.json"

        if not session_file.exists():
            return None

        with open(session_file, 'r') as f:
            data = json.load(f)

        return cls(
            session_id=data["session_id"],
            provider=data["provider"],
            model=data["model"],
            conversation=Conversation.from_dict(data["conversation"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"]
        )

    @classmethod
    def create(cls, provider: str, model: str) -> 'Session':
        """Create a new session using the bootstrap singleton's session ID.

        Previously this generated its own strftime-based ID, producing
        collisions when two sessions started in the same second and
        diverging from the rest of the codebase. Now reads
        ``get_session_id()`` — a UUID-based ID generated at bootstrap
        import time — so every consumer that talks about "the current
        session" agrees on the identifier.
        """
        return cls(
            session_id=get_session_id(),
            provider=provider,
            model=model,
        )

    @classmethod
    def resume(cls, session_id: str) -> Optional['Session']:
        """Resume a session: update bootstrap identity, restore cost,
        reconstruct the per-conversation record from disk.

        Ch03 round-2 (R2.2): single entry point that keeps the three
        operations in lockstep (CC-34 single-setter discipline at the
        resume layer). Callers (REPL ``/resume``, headless / SDK)
        should use this rather than calling ``Session.load`` plus
        ``switch_session`` plus ``restore_cost_state_for_session``
        independently.

        Order matters: ``switch_session`` fires BEFORE
        ``restore_cost_state_for_session`` so any subscriber that reads
        ``get_session_id()`` during the cost restore sees the loaded id.
        """
        from src.bootstrap.state import SessionId, switch_session
        from src.services.cost_restore import restore_cost_state_for_session

        loaded = cls.load(session_id)
        if loaded is None:
            return None
        switch_session(SessionId(session_id))
        restore_cost_state_for_session(session_id)
        return loaded

    @classmethod
    def resume_with_tail(
        cls, session_id: str
    ) -> tuple[Optional['Session'], Any | None]:
        """Resume a session and optionally attach a TailFollower.

        Returns ``(session, tail_follower)`` where *session* is the
        reconstructed :class:`Session` (or ``None`` if not found) and
        *tail_follower* is a :class:`~src.services.tail_follower.TailFollower`
        started from the current transcript end (so only *new* lines
        written by the backgrounded agent are yielded), or ``None``
        if the transcript file does not exist or the import fails.
        """
        session = cls.resume(session_id)
        if session is None:
            return None, None

        # Check for a running background agent
        try:
            from src.agent.background_runner import get_background_runner_status
            bg_status = get_background_runner_status(session_id)
            logger.info(
                "resume_with_tail: session=%s, bg_status=%s",
                session_id, bg_status,
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


def _snapshot_cost_block() -> dict:
    """Build the cost block written by ``Session.save``.

    Shape matches the reader at
    ``src/services/cost_restore.py:restore_cost_state_for_session``.
    Module-private; tests can call via the public ``Session.save``.
    """
    return {
        "total_cost_usd": get_total_cost_usd(),
        "total_api_duration": get_total_api_duration(),
        "total_api_duration_without_retries":
            get_total_api_duration_without_retries(),
        "total_tool_duration": get_total_tool_duration(),
        "total_lines_added": get_total_lines_added(),
        "total_lines_removed": get_total_lines_removed(),
        # last_duration = elapsed since start_time. cost_restore uses
        # this to back-date the new session's start_time so post-resume
        # duration accumulators continue from where they left off.
        "last_duration": time.time() - get_start_time(),
        "model_usage": {
            model: {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_creation_input_tokens": u.cache_creation_input_tokens,
                "cache_read_input_tokens": u.cache_read_input_tokens,
                "cost_usd": u.cost_usd,
            }
            for model, u in get_model_usage().items()
        },
    }
