"""Session lifecycle manager for the Direct Connect server.

Reverse-engineered from ``main.tsx:3968`` which imports
``./server/sessionManager.js``. The TS source isn't in this snapshot;
the contract from the client side (``directConnectManager.ts``) plus
``main.tsx`` flag handling implies:

  - Map ``session_id`` → spawned agent subprocess.
  - Track ``SessionState`` (5-state lifecycle from ``types.py``).
  - Persist to ``SessionIndex`` so a server restart can resume.
  - Enforce ``--max-sessions`` cap.
  - Idle-timeout for ``DETACHED`` sessions (the user navigated away
    but didn't kill the session).

This implementation is intentionally minimal — the core fan-out
(WS-frame → subprocess-stdin and subprocess-stdout → WS-frame) is
implemented; advanced features (worktree, mid-session permission-mode
change) are deferred to WI-1.9.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path

from .session_index import (
    DEFAULT_INDEX_PATH,
    add_entry,
    remove_entry,
    update_last_active,
)
from .types import SessionIndexEntry, SessionInfo, SessionState

logger = logging.getLogger(__name__)


@dataclass
class SessionManager:
    """In-memory + on-disk Direct Connect session registry.

    The manager owns the map of live sessions; the server-loop module
    drives them via ``create_session``, ``mark_running``,
    ``mark_detached``, ``mark_stopped``, and ``stop_session``.
    """

    workspace: str
    max_sessions: int | None = None
    idle_timeout_ms: int = 0
    index_path: Path = DEFAULT_INDEX_PATH
    _sessions: dict[str, SessionInfo] = field(default_factory=dict)

    # ─── Mutators ──────────────────────────────────────────────────────

    def create_session(
        self,
        *,
        cwd: str | None = None,
        permission_mode: str | None = None,
    ) -> SessionInfo:
        """Allocate a new session ID and record it in STARTING state.

        Raises ``RuntimeError`` if ``max_sessions`` would be exceeded.
        Caller is responsible for actually spawning the agent
        subprocess and then calling ``attach_process`` + ``mark_running``.
        """
        if self.max_sessions is not None and self._active_count() >= self.max_sessions:
            raise RuntimeError(
                f'Direct Connect server: max_sessions ({self.max_sessions}) reached'
            )
        sid = f'ds_{_uuid.uuid4().hex}'
        now = time.time()
        info = SessionInfo(
            id=sid,
            status=SessionState.STARTING,
            created_at=now,
            work_dir=cwd or self.workspace,
            last_active_at=now,
        )
        self._sessions[sid] = info
        # Persist so a server restart can resume — even before the
        # subprocess actually starts. The transcript_session_id
        # initially equals the session_id; if the subprocess uses a
        # different transcript ID, the server can update via
        # ``update_transcript_id`` (out of scope for this minimal cut).
        add_entry(
            SessionIndexEntry(
                session_id=sid,
                transcript_session_id=sid,
                cwd=info.work_dir,
                created_at=info.created_at,
                last_active_at=info.created_at,
                permission_mode=permission_mode,
            ),
            path=self.index_path,
        )
        return info

    def attach_process(
        self,
        session_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            raise KeyError(f'unknown session: {session_id}')
        info.process = process

    def mark_running(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            return
        now = time.time()
        info.status = SessionState.RUNNING
        info.last_active_at = now
        update_last_active(session_id, now, path=self.index_path)

    def mark_detached(self, session_id: str) -> None:
        info = self._sessions.get(session_id)
        if info is None:
            return
        now = time.time()
        info.status = SessionState.DETACHED
        info.last_active_at = now
        update_last_active(session_id, now, path=self.index_path)

    def touch(self, session_id: str) -> None:
        """Bump ``last_active_at`` (e.g., on each message exchange).

        Use from server hot paths to keep the idle reaper accurate.
        """
        info = self._sessions.get(session_id)
        if info is None:
            return
        info.last_active_at = time.time()

    async def stop_session(self, session_id: str) -> None:
        """Transition through STOPPING → STOPPED, killing the subprocess."""
        info = self._sessions.get(session_id)
        if info is None:
            return
        info.status = SessionState.STOPPING
        proc = info.process
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                # Give the agent a chance to flush; SIGKILL after 5s.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        info.status = SessionState.STOPPED
        info.process = None
        remove_entry(session_id, path=self.index_path)
        self._sessions.pop(session_id, None)

    # ─── Read-side helpers ────────────────────────────────────────────

    def get(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def active_session_ids(self) -> list[str]:
        return [
            sid
            for sid, info in self._sessions.items()
            if info.status in (SessionState.STARTING, SessionState.RUNNING, SessionState.DETACHED)
        ]

    def _active_count(self) -> int:
        return len(self.active_session_ids())

    # ─── Idle-timeout sweeper ─────────────────────────────────────────

    async def reap_idle_detached(self, now: float | None = None) -> list[str]:
        """Stop any DETACHED session whose ``last_active_at`` is past idle_timeout.

        ``idle_timeout_ms == 0`` disables the sweep (per ``ServerConfig``).
        Returns the list of session IDs that were stopped. Compares
        against ``last_active_at`` (NOT ``created_at``) so a long-lived
        active-then-detached session isn't reaped purely because it was
        created hours ago.
        """
        if self.idle_timeout_ms <= 0:
            return []
        now_t = now if now is not None else time.time()
        cutoff = now_t - (self.idle_timeout_ms / 1000.0)
        stopped: list[str] = []
        for sid, info in list(self._sessions.items()):
            if info.status != SessionState.DETACHED:
                continue
            if info.last_active_at < cutoff:
                await self.stop_session(sid)
                stopped.append(sid)
        return stopped


__all__ = ['SessionManager']
