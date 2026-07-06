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


class TestSummaryContent:
    """critic C5-P1 #1/#2: the model-facing summary must be shell-specific
    ("Background command"), carry the exit code, and be XML-escaped."""

    def test_completed_summary_has_prefix_and_exit_code(self):
        from src.utils.task_notification import build_shell_notification_xml
        x = build_shell_notification_xml(
            task_id="b1", description="run tests", status="completed",
            output_file="/tmp/b1.log", exit_code=0)
        assert '<summary>Background command "run tests" completed (exit code 0)</summary>' in x
        assert "Agent" not in x  # not the agent builder

    def test_failed_summary_has_exit_code_not_unknown_error(self):
        from src.utils.task_notification import build_shell_notification_xml
        x = build_shell_notification_xml(
            task_id="b2", description="build", status="failed",
            output_file="/tmp/b2.log", exit_code=1)
        assert '"build" failed with exit code 1' in x
        assert "Unknown error" not in x

    def test_killed_summary(self):
        from src.utils.task_notification import build_shell_notification_xml
        x = build_shell_notification_xml(
            task_id="b3", description="server", status="killed",
            output_file="/tmp/b3.log")
        assert '"server" was stopped' in x

    def test_metachars_escaped(self):
        # #2: a bg command with < > & must not produce a malformed envelope
        from src.utils.task_notification import build_shell_notification_xml
        x = build_shell_notification_xml(
            task_id="b4", description="cat <in >out && echo hi", status="completed",
            output_file="/tmp/b4.log", exit_code=0)
        assert "&lt;in &gt;out &amp;&amp;" in x
        assert "<in >out" not in x  # raw metachars would break the XML


class TestSpawnReapIntegration:
    """critic #4: a REAL spawn_background_bash → reap → notification, not just
    the unit builder."""

    def test_bg_bash_completion_notifies_the_model(self, tmp_path):
        import time
        from pathlib import Path

        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.tools.bash.background import spawn_background_bash

        clear_pending_notifications()
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        out = spawn_background_bash(
            command="exit 3", cwd=Path(str(tmp_path)),
            description="quick fail", context=ctx,
        )
        task_id = out["backgroundTaskId"]
        # wait for the reap thread to deliver
        for _ in range(100):
            if peek_pending_notifications():
                break
            time.sleep(0.05)
        q = peek_pending_notifications()
        assert q, "no completion notification delivered"
        joined = "\n".join(str(n) for n in q)
        assert 'Background command "quick fail" failed with exit code 3' in joined
        clear_pending_notifications()


class TestKillSuppressesNotification:
    """critic #3: a user kill (stop_background_bash) sets notified=True, so the
    reaper sends NO notification — matching TS killTask."""

    def test_stop_task_kill_suppresses_notification(self, tmp_path):
        # THE PRODUCTION PATH (what TaskStop uses): stop_task → LocalShellTask.kill.
        # The critic reproduced a spurious "failed with exit code -15" 8/8 here
        # because the mark used to live only in stop_background_bash, which
        # stop_task bypasses. The mark is now in LocalShellTask.kill BEFORE the
        # signal → the reaper's notification no-ops.
        import asyncio
        import time
        from pathlib import Path

        from src.tasks.stop_task import stop_task
        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.tools.bash.background import spawn_background_bash

        clear_pending_notifications()
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        out = spawn_background_bash(
            command="sleep 30", cwd=Path(str(tmp_path)),
            description="long job", context=ctx,
        )
        task_id = out["backgroundTaskId"]
        asyncio.run(stop_task(task_id, ctx))
        st = ctx.runtime_tasks.get(task_id)
        assert st is not None and st.status == "killed" and st.notified is True
        time.sleep(0.4)  # let the reaper settle
        assert peek_pending_notifications() == [], \
            f"kill produced a spurious notification: {peek_pending_notifications()}"
        clear_pending_notifications()

    def test_direct_stop_background_bash_also_marks(self, tmp_path):
        # the defense-in-depth mark in stop_background_bash still works directly
        import time
        from pathlib import Path

        from src.tool_system.context import ToolContext, ToolUseOptions
        from src.tool_system.tools.bash.background import (
            spawn_background_bash,
            stop_background_bash,
        )

        clear_pending_notifications()
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        out = spawn_background_bash(
            command="sleep 30", cwd=Path(str(tmp_path)),
            description="long job", context=ctx,
        )
        task_id = out["backgroundTaskId"]
        assert stop_background_bash(ctx, task_id) is True
        st = ctx.runtime_tasks.get(task_id)
        assert st is not None and st.status == "killed" and st.notified is True
        time.sleep(0.3)
        assert peek_pending_notifications() == []
        clear_pending_notifications()
