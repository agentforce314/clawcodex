"""Session-persistence producer — the writer side of ``SessionStorage``.

Until this phase, the session-persistence subsystem was consumer-complete but
producer-less: ``SessionStorage`` (metadata + JSONL transcript), ``resume_session``,
``SessionStorage.list_sessions`` and the TUI resume screen all existed, but **nothing
in production wrote** metadata or transcripts. :class:`SessionPersister` is that
producer; the TUI agent bridge drives it (user message at submit, assistant/tool
messages as the loop drains them, flush at the end of every run incl. abort/error).

Properties:
  * **Never raises.** Persistence must never break the live session — every method
    swallows exceptions, logging a single warning the first time (latched).
  * **Title-preserving.** ``start()`` initializes metadata only when absent, so a
    ``/rename``-set title (or any prior metadata) survives restarts; ``model``/``cwd``
    reflect the first run (``last_updated``/``message_count`` bump on every flush).
  * **Thread-safe.** Records arrive from the agent worker thread; an internal lock
    serializes buffer appends and flushes.
  * **Privacy note:** conversation content lands under ``~/.clawcodex/sessions/{id}/``
    — the same always-on persistence TS ships (``utils/sessionStorage.ts``); large
    tool results are file-ref-replaced by ``SessionStorage`` before writing.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionPersister:
    """Best-effort, never-raising producer for ``SessionStorage``."""

    def __init__(self, session_id: str, sessions_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._warned = False
        self._storage: Any = None
        try:
            from src.services.session_storage import SessionStorage

            self._storage = SessionStorage(
                session_id=str(session_id), sessions_dir=sessions_dir
            )
        except Exception as exc:  # pragma: no cover — import/ctor failure
            self._warn(exc)

    # ---- internal -------------------------------------------------------
    def _warn(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            logger.warning("Session persistence disabled for this session: %s", exc)

    # ---- producer API ---------------------------------------------------
    def start(self, *, model: str = "", cwd: str = "") -> None:
        """Initialize metadata — only when absent (never clobbers a title)."""
        try:
            with self._lock:
                if self._storage is None:
                    return
                if self._storage.get_metadata() is None:
                    self._storage.init_metadata(model=model, cwd=cwd)
        except Exception as exc:
            self._warn(exc)

    def record(self, message: Any) -> None:
        """Buffer one message (attr- or Mapping-shaped role/content)."""
        try:
            with self._lock:
                if self._storage is None:
                    return
                self._storage.write_message(message)
        except Exception as exc:
            self._warn(exc)

    def record_user(self, prompt: str) -> None:
        self.record({"role": "user", "content": prompt})

    def flush(self) -> None:
        """Flush the buffer to disk (also bumps metadata counters)."""
        try:
            with self._lock:
                if self._storage is None:
                    return
                self._storage.flush()
        except Exception as exc:
            self._warn(exc)


__all__ = ["SessionPersister"]
