"""Chapter C5 Part 1 — background-bash completion notification.

TS notifies when a run_in_background bash task reaches a terminal state
(utils/task/framework.ts:289 + BashTool/prompt.ts:285-287 "you will be
notified when it completes — do not poll"). The port marked the terminal
state notified=True WITHOUT sending (the forward-trap) → a bg bash finishing
while the model was away went unannounced. enqueue_shell_notification closes
it: atomic check-and-set on LocalShellTaskState + enqueue, duplicate-safe.
"""
from __future__ import annotations

import pytest

from src.tasks.local_shell import LocalShellTaskState
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    peek_pending_notifications,
)
from src.utils.task_notification import enqueue_shell_notification


class _Registry:
    """Minimal RuntimeTaskRegistry-shaped stub: update(id, mutator)."""
    def __init__(self, state):
        self._states = {state.id: state}

    def update(self, task_id, mutator):
        cur = self._states.get(task_id)
        if cur is not None:
            self._states[task_id] = mutator(cur)

    def get(self, task_id):
        return self._states.get(task_id)


@pytest.fixture(autouse=True)
def _clean_queue():
    clear_pending_notifications()
    yield
    clear_pending_notifications()


def _shell(task_id="b1", notified=False):
    return LocalShellTaskState(
        id=task_id, description="run tests", command="pytest",
        output_path=f"/tmp/{task_id}.log", output_file=f"/tmp/{task_id}.log",
        status="completed", exit_code=0, notified=notified, start_time=0.0,
    )


def test_enqueues_on_terminal_and_marks_notified():
    reg = _Registry(_shell())
    sent = enqueue_shell_notification(
        task_id="b1", description="run tests", status="completed",
        output_file="/tmp/b1.log", registry=reg,
    )
    assert sent is True
    assert reg.get("b1").notified is True  # check-and-set
    q = peek_pending_notifications()
    assert q and any("b1" in str(n) for n in q)  # the completion XML enqueued


def test_duplicate_safe_no_second_notification():
    reg = _Registry(_shell(notified=True))  # already notified (e.g. TaskStop did it)
    sent = enqueue_shell_notification(
        task_id="b1", description="run tests", status="completed",
        output_file="/tmp/b1.log", registry=reg,
    )
    assert sent is False
    assert peek_pending_notifications() == []  # no duplicate


def test_wrong_task_type_not_notified():
    from types import SimpleNamespace

    class _Reg:
        def __init__(self): self.state = SimpleNamespace(notified=False)
        def update(self, tid, mut): self.state = mut(self.state)
    reg = _Reg()
    sent = enqueue_shell_notification(
        task_id="x", description="d", status="completed",
        output_file="/tmp/x.log", registry=reg,
    )
    assert sent is False  # not a LocalShellTaskState → no-op
    assert reg.state.notified is False
