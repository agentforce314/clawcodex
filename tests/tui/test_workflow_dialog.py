"""Tests for the /workflows TUI dialog + render helper (#1)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_workflow import register_workflow_task
from src.tui.screens.workflow_dialog import (
    WorkflowDetailScreen,
    WorkflowsScreen,
    format_workflow_detail,
)
from src.workflow.progress import WorkflowProgress


class _FakeController:
    def __init__(self):
        self.aborted = None

    def abort(self, reason=None):
        self.aborted = reason


class _FakeRun:
    def __init__(self):
        self.controller = _FakeController()


def _registry_with_run(task_id="wdlg1"):
    reg = RuntimeTaskRegistry()
    prog = WorkflowProgress([{"title": "Search"}])
    prog.start_phase("Search")
    rec = prog.agent_started(0, "finder", "Search", "0")
    prog.agent_finished(rec, status="completed", tokens=5)
    register_workflow_task(
        task_id=task_id,
        run_id="r1",
        workflow_name="demo",
        description="demo run",
        output_file="/tmp/x.json",
        progress=prog,
        run=_FakeRun(),
        registry=reg,
    )
    return reg


def test_format_workflow_detail_renders_phases_and_agents():
    reg = _registry_with_run()
    lines = format_workflow_detail(reg.get("wdlg1"))
    blob = "\n".join(lines)
    assert "demo" in blob
    assert "Search" in blob
    assert "finder" in blob
    assert "completed" in blob


def test_format_workflow_detail_handles_no_phases():
    reg = RuntimeTaskRegistry()
    register_workflow_task(
        task_id="empty", run_id="r0", workflow_name="w", description="d",
        output_file="/tmp/y", progress=WorkflowProgress(), run=_FakeRun(), registry=reg,
    )
    lines = format_workflow_detail(reg.get("empty"))
    assert any("no phases" in line.lower() for line in lines)


class _Host(App):
    def __init__(self, registry):
        super().__init__()
        self._wf_registry = registry  # NB: not `_registry` — Textual's App owns that

    def on_mount(self):
        self.push_screen(WorkflowsScreen(registry=self._wf_registry))


async def test_workflows_screen_lists_runs():
    reg = _registry_with_run()
    app = _Host(reg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WorkflowsScreen)
        # the run is listed in the select
        assert app.screen._select is not None
        labels = [o.label for o in app.screen._select.options]
        assert any("demo" in label for label in labels)


async def test_workflows_screen_stop_kills_run():
    reg = _registry_with_run()
    app = _Host(reg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")  # stop the highlighted run
        await pilot.pause()
    state = reg.get("wdlg1")
    assert state.status == "killed"


async def test_status_line_workflow_pill_counts():
    from pathlib import Path

    from src.tool_system.context import ToolContext
    from src.tui.widgets.status_line import StatusLine

    reg = _registry_with_run()  # one running workflow
    status_line = StatusLine(provider="p", model="m", workspace_root=Path("."))

    class _SLHost(App):
        def __init__(self):
            super().__init__()
            self.tool_context = ToolContext(workspace_root=Path("."), runtime_tasks=reg)

        def compose(self):
            yield status_line

    app = _SLHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        status_line._tick()  # deterministic count refresh
        assert status_line.workflows == 1


async def test_detail_screen_opens_with_agents():
    reg = _registry_with_run()

    class _DetailHost(App):
        def on_mount(self):
            self.push_screen(WorkflowDetailScreen(registry=reg, task_id="wdlg1"))

    app = _DetailHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WorkflowDetailScreen)
        # the agent select lists the one finished agent
        assert app.screen._select is not None
        assert len(app.screen._select.options) == 1
