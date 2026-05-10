"""Tests for ``src.server.lockfile.ServerLockfile``."""

from __future__ import annotations

import pytest

from src.server.lockfile import LockfileBusyError, ServerLockfile


def test_acquire_release(tmp_path):
    p = tmp_path / 'server.lock'
    lock = ServerLockfile(p)
    lock.acquire()
    lock.release()
    # Idempotent release.
    lock.release()


def test_double_acquire_raises_busy(tmp_path):
    """Two ServerLockfile instances on the same path can't both hold it."""
    p = tmp_path / 'server.lock'
    lock1 = ServerLockfile(p)
    lock2 = ServerLockfile(p)

    lock1.acquire()
    try:
        with pytest.raises(LockfileBusyError):
            lock2.acquire()
    finally:
        lock1.release()


def test_lock_releasable_for_subsequent_acquire(tmp_path):
    p = tmp_path / 'server.lock'
    lock1 = ServerLockfile(p)
    lock1.acquire()
    lock1.release()

    lock2 = ServerLockfile(p)
    lock2.acquire()  # should succeed
    lock2.release()


def test_context_manager(tmp_path):
    p = tmp_path / 'server.lock'
    with ServerLockfile(p):
        # Inside the context, another lockfile is busy.
        other = ServerLockfile(p)
        with pytest.raises(LockfileBusyError):
            other.acquire()
    # After exit, lock is released.
    third = ServerLockfile(p)
    third.acquire()
    third.release()
