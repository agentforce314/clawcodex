"""TASKS-1 — killShellTasksForAgent port.

A sub-agent that spawns a ``run_in_background`` bash must reap it on exit, or
the shell outlives the agent as a PPID=1 zombie (the "10-day fake-logs.sh"
case). Port of typescript/src/tasks/LocalShellTask/killShellTasks.ts:53.
"""
from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from src.task_registry import RuntimeTaskRegistry
from src.tasks.local_shell import (
    LocalShellTaskState,
    kill_shell_tasks_for_agent,
)


def _spawn(cmd: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-lc", cmd], stdin=subprocess.DEVNULL, start_new_session=True
    )


def _task(task_id: str, proc: subprocess.Popen, agent_id, status="running"):
    return LocalShellTaskState(
        id=task_id, type="local_bash", status=status, description="x",
        start_time=time.time(), output_file=f"/tmp/{task_id}", pid=proc.pid,
        proc=proc, agent_id=agent_id,
    )


def test_owner_field_defaults_none():
    p = _spawn("true")
    try:
        assert _task("t", p, None).agent_id is None
    finally:
        p.terminate()


def test_reaps_owning_agents_running_bash():
    reg = RuntimeTaskRegistry()
    proc = _spawn("while true; do sleep 1; done")  # non-terminating (the zombie case)
    reg.upsert(_task("t1", proc, "agentA"))
    assert proc.poll() is None
    asyncio.run(kill_shell_tasks_for_agent("agentA", reg))
    time.sleep(0.5)
    assert proc.poll() is not None  # killed


def test_does_not_touch_other_agents_or_main_session():
    reg = RuntimeTaskRegistry()
    other = _spawn("sleep 30")
    main = _spawn("sleep 30")
    reg.upsert(_task("other", other, "agentB"))
    reg.upsert(_task("main", main, None))  # main session — no owner
    try:
        asyncio.run(kill_shell_tasks_for_agent("agentA", reg))  # neither owned by A
        time.sleep(0.3)
        assert other.poll() is None  # different agent — untouched
        assert main.poll() is None   # main session — untouched
    finally:
        other.terminate()
        main.terminate()


def test_skips_already_completed_tasks():
    reg = RuntimeTaskRegistry()
    done = _spawn("true")
    done.wait()
    reg.upsert(_task("done", done, "agentA", status="completed"))
    # must not raise even though proc already exited
    asyncio.run(kill_shell_tasks_for_agent("agentA", reg))


def test_never_raises_on_empty_registry():
    asyncio.run(kill_shell_tasks_for_agent("nobody", RuntimeTaskRegistry()))


def test_spawn_stamps_agent_id_from_context():
    # spawn_background_bash stamps agent_id from ToolContext.agent_id
    from types import SimpleNamespace
    from src.tool_system.tools.bash import background

    import inspect

    src = inspect.getsource(background.spawn_background_bash)
    assert 'agent_id=getattr(context, "agent_id", None)' in src


class _FakeProvider:
    def __init__(self, model="claude-sonnet-4-20250514"):
        self.model = model

    def get_available_models(self):
        return [self.model]


def test_core_run_agent_finally_reaps_all_paths(monkeypatch):
    """Relocation regression: the reap lives in the CORE run_agent generator's
    finally, so SYNC (inline) + workflow sub-agents — which bypass the async
    wrapper — are covered too (critic MAJOR). Verify the finally calls the reap
    with the sub-agent's own agent_id on a plain run_agent invocation."""
    from pathlib import Path

    from src.agent.run_agent import RunAgentParams, run_agent
    from src.agent.agent_definitions import EXPLORE_AGENT
    from src.tool_system.context import ToolContext, ToolUseOptions
    from src.tool_system.defaults import build_default_registry

    called = {}

    async def _spy(agent_id, registry):
        called["agent_id"] = agent_id

    # function-level import in run_agent's finally re-reads this attribute
    monkeypatch.setattr("src.tasks.local_shell.kill_shell_tasks_for_agent", _spy)

    async def _fake_query(qp):
        return
        yield  # empty async generator → run_agent falls straight to finally

    ctx = ToolContext(workspace_root=Path("/tmp"))
    ctx.options = ToolUseOptions(tools=[])
    params = RunAgentParams(
        parent_context=ctx,
        agent_definition=EXPLORE_AGENT,
        prompt="x",
        available_tools=[],
        tool_registry=build_default_registry(),
        provider=_FakeProvider(),
        agent_id="sync-agent-1",
    )

    async def _drain():
        async for _ in run_agent(params):
            pass

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.query.query.query", _fake_query
    ):
        asyncio.run(_drain())

    assert called.get("agent_id") == "sync-agent-1"
