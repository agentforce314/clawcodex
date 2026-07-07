"""Direct Connect type definitions.

Ports ``typescript/src/server/types.ts``: ``ServerConfig``,
``SessionState`` (5-state lifecycle), ``SessionInfo``,
``SessionIndexEntry``, and the response-validation helper for
``POST /sessions``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


class SessionState(str, Enum):
    """5-state Direct Connect session lifecycle.

    Mirrors ``server/types.ts:26-31``. Values are strings so they
    serialize cleanly through ``~/.clawcodex/server-sessions.json``.
    """

    STARTING = 'starting'
    RUNNING = 'running'
    DETACHED = 'detached'
    STOPPING = 'stopping'
    STOPPED = 'stopped'


@dataclass(frozen=True)
class ServerConfig:
    """Direct Connect server configuration.

    Mirrors ``server/types.ts:13-24``. The ``unix`` field selects the
    Unix-domain-socket variant (``--unix /path/to/sock``); when set,
    ``host``/``port`` are ignored. ``idle_timeout_ms = 0`` means
    "never expire detached sessions."
    """

    port: int = 0
    host: str = '127.0.0.1'
    auth_token: str = ''
    unix: str | None = None
    idle_timeout_ms: int = 0
    max_sessions: int | None = None
    workspace: str | None = None


@dataclass
class SessionInfo:
    """In-memory record of a server-side session.

    Mirrors ``server/types.ts:33-41``. ``process`` is a Popen-equivalent
    handle (``asyncio.subprocess.Process``); ``None`` when the session
    is being torn down.

    ``last_active_at`` is bumped on state transitions and per-message
    activity; the idle-timeout reaper compares against this field, NOT
    against ``created_at`` — otherwise a long-lived active-then-detached
    session would be reaped purely because it was created hours ago.
    """

    id: str
    status: SessionState
    created_at: float
    work_dir: str
    process: asyncio.subprocess.Process | None = None
    session_key: str | None = None
    last_active_at: float = 0.0  # initialized to created_at by SessionManager.create_session


@dataclass(frozen=True)
class SessionIndexEntry:
    """On-disk record persisted to ``~/.clawcodex/server-sessions.json``.

    Mirrors ``server/types.ts:46-55``. Used to resume sessions across
    server restarts: the new server reads this file at startup and can
    restart the agent subprocess with ``--resume {transcriptSessionId}``.
    """

    session_id: str
    transcript_session_id: str
    cwd: str
    created_at: float
    last_active_at: float
    permission_mode: str | None = None


# ─── Wire schema for POST /sessions response ──────────────────────────────


class ConnectResponse(TypedDict, total=False):
    """``POST /sessions`` response shape.

    Mirrors zod ``connectResponseSchema`` at ``server/types.ts:5-11``.
    """

    session_id: str
    ws_url: str
    work_dir: str  # optional


def validate_connect_response(payload: object) -> ConnectResponse:
    """Validate a ``POST /sessions`` response payload.

    Raises ``ValueError`` on missing required fields or wrong types.
    Returns the payload typed as ``ConnectResponse`` (caller still
    treats it as a dict; the TypedDict is documentation).
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f'connect response must be an object, got {type(payload).__name__}'
        )
    sid = payload.get('session_id')
    if not isinstance(sid, str) or not sid:
        raise ValueError('connect response missing session_id (non-empty string)')
    ws_url = payload.get('ws_url')
    if not isinstance(ws_url, str) or not ws_url:
        raise ValueError('connect response missing ws_url (non-empty string)')
    # work_dir is optional
    work_dir = payload.get('work_dir')
    if work_dir is not None and not isinstance(work_dir, str):
        raise ValueError('connect response work_dir must be a string when present')
    out: ConnectResponse = {'session_id': sid, 'ws_url': ws_url}
    if isinstance(work_dir, str):
        out['work_dir'] = work_dir
    return out


__all__ = [
    'ConnectResponse',
    'ServerConfig',
    'SessionIndexEntry',
    'SessionInfo',
    'SessionState',
    'validate_connect_response',
]
