"""Single-server-instance lockfile (POSIX flock; no-op on Windows).

Reverse-engineered from ``main.tsx:3982`` which imports
``./server/lockfile.js``. The TS source isn't in this snapshot; the
contract from context is: prevent two ``claude server`` invocations
from competing for the same ``--port`` (or default port).

POSIX ``fcntl.flock(LOCK_EX | LOCK_NB)`` is automatically released by
the kernel when the holding process exits, so stale-lock-after-crash
is automatic.

Direct Connect server is not supported on Windows in production, so
the no-op there is acceptable; a Windows port would need
``msvcrt.locking`` or a sentinel-PID file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class LockfileBusyError(RuntimeError):
    """Another process holds the server lockfile."""


class ServerLockfile:
    """Acquire/release context for ``~/.claude/server.lock``.

    Usage:
        async with ServerLockfile(path).hold():
            await server.serve_forever()

    Or imperatively:
        lock = ServerLockfile(path)
        lock.acquire()  # raises LockfileBusyError if held
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the lock (LOCK_EX | LOCK_NB).

        Raises ``LockfileBusyError`` if another process holds it.
        ``OSError`` on filesystem failures (no permissions, missing
        parent directory after the mkdir attempt, etc.) — those
        propagate so the caller can decide how to surface.
        """
        try:
            import fcntl
        except ImportError:
            # Windows: no-op. We still create the file so subsequent
            # ``release`` is symmetric.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LockfileBusyError(
                f'Another claude server instance holds {self._path}'
            ) from exc
        self._fd = fd

    def release(self) -> None:
        """Release the lock and close the FD. Idempotent."""
        if self._fd is None:
            return
        try:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
        except ImportError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        # Best-effort unlink so the file doesn't accumulate over restarts.
        try:
            self._path.unlink()
        except OSError:
            pass

    def __enter__(self) -> ServerLockfile:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


__all__ = ['LockfileBusyError', 'ServerLockfile']
