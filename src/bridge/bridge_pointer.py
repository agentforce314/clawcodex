"""Crash-recovery pointer for perpetual-mode bridges.

The pointer is a small JSON file written under the daemon's working
directory at ``<dir>/.claude/bridge-pointer.json``. It records the
identity (``bridge_id``, ``environment_id``, ``session_id``) of the
currently-running bridge so that a subsequent restart (after a clean
shutdown OR a crash) can:

* Reuse the same ``environment_id`` via ``reuse_environment_id`` —
  the server may resurrect the env state if the lease hasn't expired.
* Resume the same ``session_id`` rather than spawning a fresh
  Claude Code subprocess (preserves history, in-flight work, etc.).
* Detect a different host or working directory (``machine_name`` /
  ``dir`` mismatch) and drop the pointer instead of corrupting another
  daemon's state.

Best-effort semantics
---------------------
Every operation in this module is best-effort:

* :func:`read_pointer` returns ``None`` on any IO/JSON failure rather
  than raising — perpetual mode degrades to "fresh env+session" if the
  pointer is unreadable.
* :func:`write_pointer` swallows IO errors with a warning log — a
  failed write means the next restart won't recover, but it doesn't
  break the running daemon.
* :func:`clear_pointer` ignores missing-file errors (no-op).

Schema (version 1)
------------------
::

    {
      "schema_version": 1,
      "bridge_id": "<uuid>",
      "environment_id": "<server-assigned>",
      "session_id": "<server-assigned or None>",
      "machine_name": "<hostname>",
      "dir": "<absolute working directory>",
      "created_at_ms": <int>,
      "updated_at_ms": <int>
    }

Mismatched ``schema_version``, ``machine_name``, or ``dir`` causes
:func:`read_pointer` to return ``None`` (treats the pointer as stale).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_POINTER_REL = os.path.join('.claude', 'bridge-pointer.json')


@dataclass
class BridgePointer:
    """In-memory representation of the pointer file. Mirrors the
    on-disk JSON schema 1:1.

    Fields
    ------
    bridge_id
        Client-generated stable identity. Survives across restarts —
        the daemon picks a new one only if the pointer is dropped.
    environment_id
        Server-assigned env id from the most recent registration.
    session_id
        Currently-active session id; may be ``None`` when no session
        is running (e.g., right after init but before first poll).
    machine_name
        Hostname captured at write time. ``read_pointer`` rejects
        the file if this doesn't match the current host.
    dir
        Working directory captured at write time. Rejected on mismatch.
    created_at_ms / updated_at_ms
        Milliseconds since epoch. ``created_at`` is set once;
        ``updated_at`` bumps on every write.
    """

    bridge_id: str
    environment_id: str
    session_id: str | None
    machine_name: str
    dir: str
    created_at_ms: int
    updated_at_ms: int

    def to_json(self) -> dict[str, object]:
        d = asdict(self)
        d['schema_version'] = _SCHEMA_VERSION
        return d


def _pointer_path(working_dir: str) -> str:
    return os.path.join(working_dir, _POINTER_REL)


def _now_ms() -> int:
    return int(time.time() * 1000)


def read_pointer(
    working_dir: str, *, machine_name: str,
) -> BridgePointer | None:
    """Return the pointer for ``working_dir`` if one exists and is
    valid for the current host, else ``None``.

    Returns ``None`` for any of: file missing, JSON malformed,
    ``schema_version`` mismatch, ``machine_name`` mismatch, ``dir``
    mismatch (the file was written by another working tree using a
    shared HOME), or any required field missing/wrong type.
    """
    path = _pointer_path(working_dir)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except OSError as err:
        logger.warning(
            '[bridge:pointer] read %s failed: %s — treating as absent',
            path, err,
        )
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        logger.warning(
            '[bridge:pointer] %s is not valid JSON (%s) — treating as absent',
            path, err,
        )
        return None
    if not isinstance(data, dict):
        return None
    if data.get('schema_version') != _SCHEMA_VERSION:
        logger.info(
            '[bridge:pointer] schema_version mismatch in %s '
            '(file=%r, expected=%r) — treating as absent',
            path, data.get('schema_version'), _SCHEMA_VERSION,
        )
        return None
    # Required string fields.
    try:
        bridge_id = str(data['bridge_id'])
        environment_id = str(data['environment_id'])
        file_machine = str(data['machine_name'])
        file_dir = str(data['dir'])
        created_at_ms = int(data['created_at_ms'])
        updated_at_ms = int(data['updated_at_ms'])
    except (KeyError, TypeError, ValueError) as err:
        logger.info(
            '[bridge:pointer] %s missing or malformed required fields '
            '(%s) — treating as absent', path, err,
        )
        return None
    session_id_raw = data.get('session_id')
    session_id = (
        str(session_id_raw) if isinstance(session_id_raw, str) else None
    )

    # Host/dir staleness check. Two daemons running in different
    # checkouts of the same repo (or different machines mounting the
    # same NFS HOME) could otherwise collide on each other's pointers.
    if file_machine != machine_name:
        logger.info(
            '[bridge:pointer] %s machine mismatch (file=%r, current=%r) '
            '— treating as absent', path, file_machine, machine_name,
        )
        return None
    if os.path.abspath(file_dir) != os.path.abspath(working_dir):
        logger.info(
            '[bridge:pointer] %s dir mismatch (file=%r, current=%r) '
            '— treating as absent', path, file_dir, working_dir,
        )
        return None

    return BridgePointer(
        bridge_id=bridge_id,
        environment_id=environment_id,
        session_id=session_id,
        machine_name=file_machine,
        dir=file_dir,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
    )


def write_pointer(
    working_dir: str, *,
    bridge_id: str,
    environment_id: str,
    session_id: str | None,
    machine_name: str,
    created_at_ms: int | None = None,
) -> None:
    """Write/overwrite the pointer for ``working_dir``.

    ``created_at_ms`` defaults to the current time on first write;
    callers preserving a pre-existing pointer's identity should pass
    its ``created_at_ms`` so the field doesn't reset on every update.

    Writes through a tmpfile + ``os.replace`` for atomicity — a crash
    during the write cannot leave a half-written pointer that
    ``read_pointer`` then parses incorrectly.
    """
    path = _pointer_path(working_dir)
    now = _now_ms()
    pointer = BridgePointer(
        bridge_id=bridge_id,
        environment_id=environment_id,
        session_id=session_id,
        machine_name=machine_name,
        dir=working_dir,
        created_at_ms=created_at_ms if created_at_ms is not None else now,
        updated_at_ms=now,
    )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as err:
        logger.warning(
            '[bridge:pointer] mkdir(%s) failed: %s — pointer not written',
            os.path.dirname(path), err,
        )
        return
    tmp_path = f'{path}.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(pointer.to_json(), fh, indent=2)
        os.replace(tmp_path, path)
    except OSError as err:
        logger.warning(
            '[bridge:pointer] write %s failed: %s — pointer not written',
            path, err,
        )
        # Best-effort cleanup of the tmpfile.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def clear_pointer(working_dir: str) -> None:
    """Remove the pointer for ``working_dir``. No-op if missing."""
    path = _pointer_path(working_dir)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as err:
        logger.warning(
            '[bridge:pointer] unlink(%s) failed: %s', path, err,
        )


__all__ = [
    'BridgePointer',
    'clear_pointer',
    'read_pointer',
    'write_pointer',
]