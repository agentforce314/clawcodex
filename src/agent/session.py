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
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from src.bootstrap.state import get_session_id

from .conversation import Conversation


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
        """Save session to disk."""
        session_dir = Path.home() / ".clawcodex" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)

        session_file = session_dir / f"{self.session_id}.json"

        session_data = {
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "conversation": self.conversation.to_dict(),
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat()
        }

        with open(session_file, 'w') as f:
            json.dump(session_data, f, indent=2)

        self.updated_at = datetime.now().isoformat()

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
