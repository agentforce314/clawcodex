"""``~/.clawcodex/server-sessions.json`` persistence for Direct Connect sessions.

Mirrors the persistence helpers implied by ``server/types.ts:46-57``:
the server reads the index at startup to resume sessions across server
restarts; reads/writes the index when sessions start/stop/become
detached.

Concurrency: server process and resuming-client process can both touch
the file. We use ``fcntl.flock(LOCK_EX)`` on POSIX (no-op on Windows
since Direct Connect is Linux/macOS-only in production). Atomic writes
via ``tempfile.mkstemp`` + ``os.replace``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from .types import SessionIndexEntry

logger = logging.getLogger(__name__)

DEFAULT_INDEX_PATH = Path.home() / '.clawcodex' / 'server-sessions.json'

#: ``SessionIndex`` is keyed by ``session_id`` (TS uses ``string`` keys
#: but the value type matches ``SessionIndexEntry``).
SessionIndex = dict[str, SessionIndexEntry]


# ─── File-lock context manager ─────────────────────────────────────────────


@contextmanager
def _flock(path: Path) -> Iterator[None]:
    """Exclusive lock on ``path`` for the duration of the ``with`` block.

    POSIX-only via ``fcntl.flock``; Windows is a no-op (Direct Connect
    server is not supported on Windows in production). Lock is auto-
    released by kernel when the FD closes (process exit included), so
    stale-lock-after-crash is automatic.
    """
    try:
        import fcntl
    except ImportError:
        # Windows: no-op. Direct Connect server isn't supported there.
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ─── Read/write ────────────────────────────────────────────────────────────


def _entry_to_dict(entry: SessionIndexEntry) -> dict[str, Any]:
    return asdict(entry)


def _dict_to_entry(payload: dict[str, Any]) -> SessionIndexEntry:
    """Strict ``dict → SessionIndexEntry`` parser; raises on unknown keys."""
    required = {'session_id', 'transcript_session_id', 'cwd', 'created_at', 'last_active_at'}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f'session index entry missing fields: {sorted(missing)}')
    return SessionIndexEntry(
        session_id=str(payload['session_id']),
        transcript_session_id=str(payload['transcript_session_id']),
        cwd=str(payload['cwd']),
        created_at=float(payload['created_at']),
        last_active_at=float(payload['last_active_at']),
        permission_mode=(
            str(payload['permission_mode'])
            if payload.get('permission_mode') is not None
            else None
        ),
    )


def load_index(path: Path = DEFAULT_INDEX_PATH) -> SessionIndex:
    """Read the index file. Missing/empty/corrupted file → empty dict.

    Best-effort: a corrupted file (invalid JSON, missing fields) is
    treated as empty rather than raising — the server can still start
    fresh.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning('[session_index] read failed: %s; returning empty index', exc)
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning('[session_index] invalid JSON: %s; returning empty index', exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning('[session_index] root is not an object; returning empty index')
        return {}
    out: SessionIndex = {}
    for key, value in parsed.items():
        if not isinstance(value, dict):
            continue
        try:
            out[str(key)] = _dict_to_entry(value)
        except (ValueError, TypeError) as exc:
            logger.warning(
                '[session_index] skipping malformed entry %r: %s', key, exc
            )
            continue
    return out


def save_index(index: SessionIndex, path: Path = DEFAULT_INDEX_PATH) -> None:
    """Atomically replace the index file with ``index`` contents.

    Uses ``tempfile.mkstemp`` in the same directory + ``os.replace``
    (POSIX atomic). The file mode is 0o600 to keep session IDs +
    cwds out of other users' view.
    """
    payload: dict[str, dict[str, Any]] = {
        sid: _entry_to_dict(entry) for sid, entry in index.items()
    }
    serialized = json.dumps(payload, sort_keys=True, indent=2).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix='.server-sessions.', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        # Mode 0o600 — owner only.
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except OSError:
        # Best-effort cleanup of the tempfile on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── Mutators ──────────────────────────────────────────────────────────────


def add_entry(entry: SessionIndexEntry, path: Path = DEFAULT_INDEX_PATH) -> None:
    """Add or replace ``entry`` in the index. Locked; atomic."""
    with _flock(path):
        index = load_index(path)
        index[entry.session_id] = entry
        save_index(index, path)


def remove_entry(session_id: str, path: Path = DEFAULT_INDEX_PATH) -> None:
    """Remove ``session_id`` from the index. Locked; atomic. No-op if absent."""
    with _flock(path):
        index = load_index(path)
        if session_id in index:
            del index[session_id]
            save_index(index, path)


def update_last_active(
    session_id: str,
    timestamp: float,
    path: Path = DEFAULT_INDEX_PATH,
) -> None:
    """Bump ``last_active_at`` for ``session_id``. Locked; atomic.

    No-op if the session isn't in the index (the server may be ahead of
    the index for a freshly-started session not yet persisted).
    """
    with _flock(path):
        index = load_index(path)
        existing = index.get(session_id)
        if existing is None:
            return
        # Frozen dataclass — replace by constructing a new instance.
        index[session_id] = SessionIndexEntry(
            session_id=existing.session_id,
            transcript_session_id=existing.transcript_session_id,
            cwd=existing.cwd,
            created_at=existing.created_at,
            last_active_at=timestamp,
            permission_mode=existing.permission_mode,
        )
        save_index(index, path)


__all__ = [
    'DEFAULT_INDEX_PATH',
    'SessionIndex',
    'add_entry',
    'load_index',
    'remove_entry',
    'save_index',
    'update_last_active',
]
