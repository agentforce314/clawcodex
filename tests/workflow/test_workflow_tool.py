"""Tests for the background launcher and the Workflow tool factory."""

from __future__ import annotations

import asyncio

import src.tasks  # noqa: F401 — task registration
from src.settings.types import SettingsSchema
from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_workflow import LocalWorkflowTaskState
from src.tool_system.context import ToolContext
from src.tool_system.tools.workflow import _resolve_source, make_workflow_tool
from src.workflow.launch import run_workflow_task

META = 'meta = {"name": "t", "description": "d"}\n'


def _enable(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    monkeypatch.setattr("src.settings.settings.get_settings", lambda **_: SettingsSchema())


# ── launcher ──────────────────────────────────────────────────────────────────


async def test_run_workflow_task_completes(make_runner):
    reg = RuntimeTaskRegistry()
    result = await run_workflow_task(
        source=META + 'return await agent("hi")',
        runner=make_runner(),
        registry=reg,
        task_id="wabc",
        run_id="wf_x",
        output_file="/tmp/x.json",
    )
    assert result.ok
    state = reg.get("wabc")
    assert isinstance(state, LocalWorkflowTaskState)
    assert state.status == "completed"
    assert state.result == "r0"
    assert state.workflow_name == "t"


async def test_run_workflow_task_meta_error_surfaces_failed_task(make_runner):
    reg = RuntimeTaskRegistry()
    await run_workflow_task(
        source="return 1",  # no meta
        runner=make_runner(),
        registry=reg,
        task_id="wbad",
        run_id="wf_y",
        output_file="/tmp/y.json",
    )
    state = reg.get("wbad")
    assert state is not None
    assert state.status == "failed"
    assert "Meta" in state.error


async def test_run_workflow_task_script_error_fails(make_runner):
    reg = RuntimeTaskRegistry()
    await run_workflow_task(
        source=META + 'raise ValueError("x")',
        runner=make_runner(),
        registry=reg,
        task_id="werr",
        run_id="wf_z",
        output_file="/tmp/z.json",
    )
    assert reg.get("werr").status == "failed"
    assert "ValueError" in reg.get("werr").error


# ── source resolution ─────────────────────────────────────────────────────────


def test_resolve_source_inline_script():
    assert _resolve_source({"script": "hello"}, None) == ("hello", None)


def test_resolve_source_script_path(tmp_path):
    p = tmp_path / "wf.py"
    p.write_text("body", encoding="utf-8")
    assert _resolve_source({"script_path": str(p)}, None) == ("body", None)


def test_resolve_source_named(tmp_path):
    wf_dir = tmp_path / ".claude" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "demo.py").write_text("named body", encoding="utf-8")
    assert _resolve_source({"name": "demo"}, tmp_path) == ("named body", None)


def test_resolve_source_errors():
    src, err = _resolve_source({}, None)
    assert src is None and "provide one" in err
    src, err = _resolve_source({"name": "nope"}, None)
    assert src is None and "no saved workflow" in err


# ── tool ──────────────────────────────────────────────────────────────────────


def test_tool_metadata_and_gating(monkeypatch):
    tool = make_workflow_tool(registry=object(), provider=None)
    assert tool.name == "Workflow"
    assert tool.is_read_only({}) is True
    _enable(monkeypatch)
    assert tool.is_enabled() is True
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    assert tool.is_enabled() is False


async def test_tool_call_disabled_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "1")
    tool = make_workflow_tool(registry=object(), provider=None, runner_factory=lambda ctx, rid: None)
    res = await tool.call({"script": META}, ToolContext(workspace_root=tmp_path))
    assert res.is_error
    assert "disabled" in res.output["error"]


async def test_tool_call_launches_and_completes(make_runner, monkeypatch, tmp_path):
    _enable(monkeypatch)
    runner = make_runner()
    tool = make_workflow_tool(registry=object(), provider=None, runner_factory=lambda ctx, rid: runner)
    ctx = ToolContext(workspace_root=tmp_path)
    res = await tool.call({"script": META + 'return await agent("hi")'}, ctx)
    assert not res.is_error
    assert res.output["status"] == "workflow_launched"
    task_id = res.output["task_id"]
    assert task_id.startswith("w")

    # Let the background task finish.
    for _ in range(200):
        state = ctx.runtime_tasks.get(task_id)
        if state is not None and state.status == "completed":
            break
        await asyncio.sleep(0.01)
    final = ctx.runtime_tasks.get(task_id)
    assert final is not None and final.status == "completed"
    assert final.result == "r0"


def test_tool_call_survives_ephemeral_dispatch_loop(make_runner, monkeypatch, tmp_path):
    # Production topology: tool dispatch drives an async `call` via `asyncio.run`
    # on a worker thread — a throwaway loop destroyed the instant `call` returns
    # the handle. The workflow must run to completion on its own daemon thread
    # regardless. (Regression for the "loop torn down before the run executes"
    # bug; this is a SYNC test on purpose so there is no live loop to lean on.)
    import time

    _enable(monkeypatch)
    runner = make_runner()
    tool = make_workflow_tool(registry=object(), provider=None, runner_factory=lambda ctx, rid: runner)
    ctx = ToolContext(workspace_root=tmp_path)

    res = asyncio.run(tool.call({"script": META + 'return await agent("hi")'}, ctx))
    assert res.output["status"] == "workflow_launched"
    task_id = res.output["task_id"]

    # The ephemeral loop is gone now; the daemon-threaded run must still finish.
    for _ in range(300):
        state = ctx.runtime_tasks.get(task_id)
        if state is not None and state.status in ("completed", "failed"):
            break
        time.sleep(0.01)
    final = ctx.runtime_tasks.get(task_id)
    assert final is not None and final.status == "completed"
    assert final.result == "r0"
