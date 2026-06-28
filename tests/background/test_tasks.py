"""Background task registry tests."""

import time

from src.background import BackgroundTasks


def _wait_done(reg, tid, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        t = reg.get(tid)
        if t and t.status != "running":
            return t
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def test_start_and_complete(tmp_path):
    reg = BackgroundTasks()
    t = reg.start("echo hello", str(tmp_path))
    assert t.status == "running"
    done = _wait_done(reg, t.id)
    assert done.status == "done"
    assert done.exit_code == 0
    assert "hello" in done.output


def test_failed_exit_code(tmp_path):
    reg = BackgroundTasks()
    t = reg.start("exit 3", str(tmp_path))
    done = _wait_done(reg, t.id)
    assert done.status == "failed"
    assert done.exit_code == 3


def test_list_and_output(tmp_path):
    reg = BackgroundTasks()
    t = reg.start("echo abc", str(tmp_path))
    _wait_done(reg, t.id)
    assert any(x.id == t.id for x in reg.list())
    assert "abc" in (reg.output(t.id) or "")


def test_kill(tmp_path):
    reg = BackgroundTasks()
    t = reg.start("sleep 10", str(tmp_path))
    assert reg.kill(t.id) is True
    done = _wait_done(reg, t.id)
    assert done.status == "killed"
