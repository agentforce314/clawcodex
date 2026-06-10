"""Tests for the ``/tasks`` command (Phase 10 — degraded background-task list).

Lists running tasks from ``context.tool_context.runtime_tasks`` (the live registry).
Same output-style/``/mcp`` pattern (``run()`` returns text, no ``ctx.ui``). REPL-functional;
SDK (``tool_context is None``) → "unavailable". Coexistence is inversion (TUI keeps panel).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.command_system import (
    TASKS_COMMAND,
    TasksCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost


def _task(id, type, status, description=""):
    return SimpleNamespace(id=id, type=type, status=status, description=description)


def _ctx(tmp_path, *, ui=None, tasks=None, tool_context="__default__"):
    if tool_context == "__default__":
        registry = SimpleNamespace(all=lambda: list(tasks or []))
        tool_context = SimpleNamespace(runtime_tasks=registry)
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, ui=ui, tool_context=tool_context
    )


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_tasks_registered():
    assert "tasks" in {c.name for c in get_builtin_commands()}
    assert "tasks" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_tasks_metadata_mirrors_ts():
    assert isinstance(TASKS_COMMAND, TasksCommand)
    assert TASKS_COMMAND.name == "tasks"
    assert TASKS_COMMAND.description == "List and manage background tasks"
    assert TASKS_COMMAND.aliases == ["bashes"]
    assert TASKS_COMMAND.command_type == CommandType.INTERACTIVE


# --------------------------------------------------------------------------- #
# B. Bridge-safety + dispatch inversion
# --------------------------------------------------------------------------- #
def test_tasks_blocked_from_bridge_by_type():
    assert is_bridge_safe_command(TASKS_COMMAND) is False


def test_dispatch_local_command_intercepts_tasks():
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/tasks", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is True
    assert res.open_dialog == "tasks"


# --------------------------------------------------------------------------- #
# C. List output. t1 = realistic (Literal[str] type/status, the production shape);
#    t2 = SYNTHETIC `.value`-bearing object to exercise the defensive _coerce branch.
# --------------------------------------------------------------------------- #
async def test_list_output(tmp_path):
    tasks = [
        _task("t1", "local_shell", "running", "build the thing"),
        _task("t2", SimpleNamespace(value="agent"), SimpleNamespace(value="done"), ""),
    ]
    out = await TASKS_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost(), tasks=tasks))
    assert isinstance(out, InteractiveOutcome)
    assert out.message == (
        "Background tasks:\n"
        "• [running] build the thing (id: t1)\n"
        "• [done] agent (id: t2)"  # description empty → falls back to coerced type
    )
    assert out.display == "system"


async def test_empty_registry(tmp_path):
    out = await TASKS_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost(), tasks=[]))
    assert out.message == "No background tasks."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# E. Unavailable when no tool_context / registry (SDK surface)
# --------------------------------------------------------------------------- #
async def test_unavailable_without_tool_context(tmp_path):
    out = await TASKS_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost(), tool_context=None))
    assert out.message == "Background tasks are unavailable on this surface."
    assert out.display == "system"


async def test_unavailable_without_runtime_tasks(tmp_path):
    tc = SimpleNamespace()  # has no runtime_tasks attr
    out = await TASKS_COMMAND.run("", _ctx(tmp_path, ui=NullUIHost(), tool_context=tc))
    assert out.message == "Background tasks are unavailable on this surface."


# --------------------------------------------------------------------------- #
# F. Engine end-to-end (headless, no raise — unlike pickers)
# --------------------------------------------------------------------------- #
async def test_engine_succeeds_headless(tmp_path):
    tasks = [_task("t1", "local_shell", "running", "do it")]
    reg = CommandRegistry()
    reg.register(TASKS_COMMAND)
    ctx = _ctx(tmp_path, tasks=tasks)  # ui=None → engine subs NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/tasks")

    assert result.success is True
    assert result.text.startswith("Background tasks:")
