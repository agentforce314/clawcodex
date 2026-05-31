from __future__ import annotations

import json

from clawcodex_ext.cron_system.lock import CronTaskLock


def test_lock_acquire_release(tmp_path) -> None:
    lock = CronTaskLock(tmp_path, "session-a")
    assert lock.acquire() is True
    assert lock.path.exists()
    lock.release()
    assert not lock.path.exists()


def test_second_identity_cannot_acquire_live_lock(tmp_path) -> None:
    first = CronTaskLock(tmp_path, "session-a")
    second = CronTaskLock(tmp_path, "session-b")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()


def test_stale_pid_lock_can_be_recovered(tmp_path) -> None:
    lock = CronTaskLock(tmp_path, "session-a")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text(json.dumps({"sessionId": "old", "pid": -1, "acquiredAt": 1}), encoding="utf-8")
    assert lock.acquire() is True
    lock.release()
