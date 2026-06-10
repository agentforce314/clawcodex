"""Tests for workflow result delivery (#7) and run-file location (#8)."""

from __future__ import annotations

import pytest

from src.agent.transcript import get_workflow_run_path
from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_workflow import (
    complete_workflow_task,
    kill_workflow_task,
    register_workflow_task,
)
from src.workflow.progress import WorkflowProgress


class _FakeRun:
    class _C:
        def abort(self, reason=None):
            pass

    controller = _C()


def _register(reg):
    return register_workflow_task(
        task_id="wnotify1",
        run_id="wf_run1",
        workflow_name="demo",
        description="demo",
        output_file="/tmp/x.json",
        progress=WorkflowProgress(),
        run=_FakeRun(),
        registry=reg,
    )


@pytest.fixture
def captured(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        "src.utils.message_queue_manager.enqueue_pending_notification",
        lambda **kw: seen.append(kw),
    )
    return seen


# ── #7 result delivery ────────────────────────────────────────────────────────


def test_completion_delivers_result_to_model(captured):
    reg = RuntimeTaskRegistry()
    state = _register(reg)
    complete_workflow_task(state.id, result={"answer": 42}, registry=reg)

    assert len(captured) == 1
    note = captured[0]
    assert note["mode"] == "task-notification"
    xml = note["value"]
    assert "completed" in xml
    assert "42" in xml  # the result is rendered into the <result> section
    assert reg.get(state.id).notified is True


def test_exactly_one_notification_even_if_complete_then_kill(captured):
    reg = RuntimeTaskRegistry()
    state = _register(reg)
    complete_workflow_task(state.id, result="done", registry=reg)
    kill_workflow_task(state.id, reg)  # late kill on a completed task
    assert len(captured) == 1  # notified flag guards duplicate delivery


def test_kill_delivers_a_killed_notification(captured):
    reg = RuntimeTaskRegistry()
    state = _register(reg)
    kill_workflow_task(state.id, reg)
    assert len(captured) == 1
    assert "killed" in captured[0]["value"]


# ── #8 run-file location ──────────────────────────────────────────────────────


def test_run_path_lives_under_session_storage():
    p = get_workflow_run_path("wf_abc123def")
    assert ".clawcodex" in p
    assert "transcripts" in p
    assert "workflows" in p
    assert p.endswith("wf_abc123def.json")


def test_run_path_rejects_traversal():
    with pytest.raises(ValueError):
        get_workflow_run_path("../../etc/passwd")
