"""Tests for the local_workflow background task + pill label."""

from __future__ import annotations

import src.tasks  # noqa: F401 — triggers Task registration
from src.task_registry import RuntimeTaskRegistry, get_task_by_type
from src.tasks_core import generate_task_id
from src.tasks.local_workflow import (
    LocalWorkflowTask,
    LocalWorkflowTaskState,
    complete_workflow_task,
    fail_workflow_task,
    kill_workflow_task,
    register_workflow_task,
    retry_workflow_agent,
    skip_workflow_agent,
    update_workflow_summary,
)
from src.tasks.pill_label import background_task_pill, workflow_pill_label
from src.workflow.progress import WorkflowProgress


class _FakeController:
    def __init__(self):
        self.aborted_with = None

    def abort(self, reason=None):
        self.aborted_with = reason


class _FakeRun:
    def __init__(self):
        self.controller = _FakeController()
        self.skipped = []
        self.retried = []

    def abort_agent(self, key):
        self.skipped.append(key)
        return key == "0"

    def retry_agent(self, key):
        self.retried.append(key)
        return key == "0"


def _register(registry, run=None, progress=None):
    progress = progress or WorkflowProgress([{"title": "P"}])
    run = run or _FakeRun()
    return register_workflow_task(
        task_id=generate_task_id("local_workflow"),
        run_id="wf123",
        workflow_name="demo",
        description="demo workflow",
        output_file="/tmp/wf.jsonl",
        progress=progress,
        run=run,
        registry=registry,
    )


def test_task_is_registered():
    task = get_task_by_type("local_workflow")
    assert task is not None
    assert task.name == "LocalWorkflowTask"
    assert task.type == "local_workflow"


def test_task_id_prefix():
    assert generate_task_id("local_workflow").startswith("w")


def test_register_and_summary():
    reg = RuntimeTaskRegistry()
    state = _register(reg)
    got = reg.get(state.id)
    assert isinstance(got, LocalWorkflowTaskState)
    assert got.status == "running"
    assert got.type == "local_workflow"
    assert got.workflow_name == "demo"
    # summary derives from the live progress
    got.progress.start_phase("P")
    update_workflow_summary(state.id, reg)
    assert "P" in reg.get(state.id).summary


def test_complete_and_fail():
    reg = RuntimeTaskRegistry()
    s1 = _register(reg)
    complete_workflow_task(s1.id, result={"ok": 1}, registry=reg)
    done = reg.get(s1.id)
    assert done.status == "completed"
    assert done.result == {"ok": 1}
    assert done.end_time is not None
    assert done.evict_after is not None

    s2 = _register(reg)
    fail_workflow_task(s2.id, error="boom", registry=reg)
    assert reg.get(s2.id).status == "failed"
    assert reg.get(s2.id).error == "boom"


def test_kill_aborts_the_run():
    reg = RuntimeTaskRegistry()
    run = _FakeRun()
    state = _register(reg, run=run)
    kill_workflow_task(state.id, reg)
    assert reg.get(state.id).status == "killed"
    assert run.controller.aborted_with == "workflow_stopped"


def test_skip_agent_targets_one_controller():
    reg = RuntimeTaskRegistry()
    run = _FakeRun()
    state = _register(reg, run=run)
    assert skip_workflow_agent(state.id, "0", reg) is True
    assert skip_workflow_agent(state.id, "9", reg) is False
    assert run.skipped == ["0", "9"]


def test_retry_agent_targets_one():
    reg = RuntimeTaskRegistry()
    run = _FakeRun()
    state = _register(reg, run=run)
    assert retry_workflow_agent(state.id, "0", reg) is True
    assert retry_workflow_agent(state.id, "9", reg) is False
    assert run.retried == ["0", "9"]


async def test_task_adapter_kill():
    reg = RuntimeTaskRegistry()
    run = _FakeRun()
    state = _register(reg, run=run)
    await LocalWorkflowTask().kill(state.id, reg)
    assert reg.get(state.id).status == "killed"
    assert run.controller.aborted_with == "workflow_stopped"


def test_terminal_transitions_are_idempotent():
    reg = RuntimeTaskRegistry()
    state = _register(reg)
    complete_workflow_task(state.id, result="a", registry=reg)
    # A later kill must not override the completed terminal state.
    kill_workflow_task(state.id, reg)
    assert reg.get(state.id).status == "completed"


# ── pill label ────────────────────────────────────────────────────────────────


def test_pill_label_counts_running_workflows():
    reg = RuntimeTaskRegistry()
    assert workflow_pill_label(reg.all()) is None
    a = _register(reg)
    assert workflow_pill_label(reg.all()) == "1 background workflow"
    _register(reg)
    assert workflow_pill_label(reg.all()) == "2 background workflows"
    # a terminal workflow drops out of the count
    complete_workflow_task(a.id, result=None, registry=reg)
    assert workflow_pill_label(reg.all()) == "1 background workflow"


def test_background_pill_combines_types():
    reg = RuntimeTaskRegistry()
    _register(reg)
    assert background_task_pill(reg.all()) == "1 background workflow"
