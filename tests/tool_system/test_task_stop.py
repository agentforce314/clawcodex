"""Tests for TaskStopTool — Phase 0 of the ch10 refactor.

Covers WI-0.1 (`stop_requested` footgun fix), WI-0.2 (`shell_id`
back-compat), and WI-0.3 (`KillShell` alias). The plan is at
``my-docs/ch10-coordination-refactoring-plan.md``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_system.context import ToolContext
from src.tool_system.tools.task_stop import TaskStopTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_root=tmp_path)


def _call_task_stop(tool_input: dict, ctx: ToolContext):
    """Wrapper for the now-async ``TaskStopTool.call`` (post Chunk D /
    WI-4.0). Tests stay readable; the wrapper drives the coroutine."""
    import asyncio

    return asyncio.run(TaskStopTool.call(tool_input, ctx))


# ---------------------------------------------------------------------------
# WI-0.1 — `stop_requested` footgun: unknown task_id must NOT take down the loop
# ---------------------------------------------------------------------------


def test_unknown_task_id_returns_error_not_global_stop(ctx: ToolContext) -> None:
    """A coordinator-style ``TaskStop({task_id: 'agent-abc'})`` against an
    unknown id must report not-found and never mutate ``stop_requested``.

    This is the chapter-10 §6a footgun fix: pre-WI-0.1 the call fell through
    to ``context.stop_requested = True`` and brought down the main loop.
    """
    result = _call_task_stop({"task_id": "agent-does-not-exist"}, ctx)

    assert result.is_error is True
    assert result.output["stopped"] is False
    assert result.output["task_id"] == "agent-does-not-exist"
    assert "No task found" in result.output["error"]
    assert getattr(ctx, "stop_requested", False) is False, (
        "TaskStop must never set stop_requested for an unknown id"
    )


def test_missing_task_id_returns_validation_error(ctx: ToolContext) -> None:
    """Empty/missing ``task_id`` is rejected before the lookup."""
    result = _call_task_stop({}, ctx)

    assert result.is_error is True
    assert result.output["stopped"] is False
    assert result.output["task_id"] is None
    assert "task_id is required" in result.output["error"]
    assert getattr(ctx, "stop_requested", False) is False


def test_empty_string_task_id_returns_validation_error(ctx: ToolContext) -> None:
    result = _call_task_stop({"task_id": "   "}, ctx)
    assert result.is_error is True
    assert "task_id is required" in result.output["error"]


def test_stop_requested_field_no_longer_referenced_in_module() -> None:
    """Belt-and-braces — the production path must not read or write
    ``context.stop_requested``. Walks the AST so docstring mentions
    (which are intentional, explaining the historical footgun) don't
    trigger a false positive."""
    import ast
    import inspect

    from src.tool_system.tools import task_stop

    tree = ast.parse(inspect.getsource(task_stop))

    for node in ast.walk(tree):
        # context.stop_requested = ... and any read of context.stop_requested
        if isinstance(node, ast.Attribute) and node.attr == "stop_requested":
            # Allow only if the .value isn't a bare ``context`` Name; this is
            # belt-and-braces, but matches the actual offending shape.
            if isinstance(node.value, ast.Name) and node.value.id == "context":
                pytest.fail(
                    "task_stop module contains a context.stop_requested "
                    "reference in executable code — the WI-0.1 fix has "
                    "regressed. Documentation in docstrings is fine; "
                    "actual reads/writes are not."
                )


# ---------------------------------------------------------------------------
# WI-0.2 — shell_id back-compat
# ---------------------------------------------------------------------------


def _seed_bash_state(ctx: ToolContext, task_id: str) -> None:
    """Seed a minimal ``LocalShellTaskState`` on ``runtime_tasks``. Used
    by the shell_id back-compat tests; mirrors what ``spawn_background_bash``
    populates, minus the live Popen handle (so ``stop_background_bash``
    returns False — which is exactly the routing-only behavior the
    test wants to verify)."""
    from src.tasks.local_shell import LocalShellTaskState

    state = LocalShellTaskState(
        id=task_id,
        type="local_bash",
        status="running",
        description="seeded test entry",
        start_time=0.0,
        output_file="/tmp/x",
        command="sleep 30",
        cwd="/tmp",
    )
    ctx.runtime_tasks.upsert(state)


def test_shell_id_resolves_to_known_bash_task(ctx: ToolContext) -> None:
    """An old ``KillShell({shell_id: ...})`` payload still routes to the
    background-bash dispatch path. Post Chunk E / WI-5.1, dispatch
    goes through ``stop_task()`` → ``runtime_tasks`` typed lookup; the
    fixture seeds the typed registry directly so the shell_id input
    path can be tested in isolation from the spawn pipeline."""
    _seed_bash_state(ctx, "b12345")

    result = _call_task_stop({"shell_id": "b12345"}, ctx)

    assert result.is_error is False
    assert result.output["task_id"] == "b12345"
    assert result.output.get("task_type") == "local_bash"
    # ``stop_background_bash`` returns False because the seeded state
    # has no live Popen — the assertion that matters is that we
    # reached the bash branch via runtime_tasks dispatch at all.
    assert result.output["stopped"] is False


def test_task_id_takes_precedence_over_shell_id(ctx: ToolContext) -> None:
    """When both fields are set, ``task_id`` wins (deterministic behavior
    for newer callers)."""
    _seed_bash_state(ctx, "b-newer")

    result = _call_task_stop(
        {"task_id": "b-newer", "shell_id": "b-older"},
        ctx,
    )

    assert result.output["task_id"] == "b-newer"


def test_unknown_shell_id_returns_error(ctx: ToolContext) -> None:
    result = _call_task_stop({"shell_id": "b-missing"}, ctx)
    assert result.is_error is True
    assert result.output["task_id"] == "b-missing"
    assert "No task found" in result.output["error"]


# ---------------------------------------------------------------------------
# WI-0.3 — KillShell alias
# ---------------------------------------------------------------------------


def test_killshell_alias_registered_on_tool() -> None:
    """The ``aliases`` tuple includes ``"KillShell"`` so the tool registry
    routes ``KillShell`` calls to ``TaskStopTool``."""
    assert "KillShell" in TaskStopTool.aliases


def test_tool_canonical_name_is_taskstop() -> None:
    """The canonical tool name stays ``"TaskStop"``; the alias is only an
    additional resolution path, not a rename."""
    assert TaskStopTool.name == "TaskStop"


# ---------------------------------------------------------------------------
# M1 regression (Chunk-B → Chunk-D simplified) — kill timeout must NOT
# silently report success
# ---------------------------------------------------------------------------


def test_async_kill_returns_error_on_timeout(ctx: ToolContext) -> None:
    """When the per-type ``Task.kill`` coroutine hangs past the 5s budget,
    TaskStop must report a tool-result error (``is_error=True`` +
    ``error="kill timed out after 5s"``) rather than silently claiming
    success on a still-running task.

    Structurally the WI-0.1 footgun's cousin. Originally implemented in
    Chunk B as a threaded ``asyncio.run`` bridge; simplified in Chunk D
    to ``await asyncio.wait_for(...)`` once WI-4.0 made the dispatch
    layer async-aware. The behavior — timeout → ``is_error=True`` +
    timeout reason — is preserved verbatim; this test guards it.
    """
    import asyncio
    import time
    from unittest.mock import patch

    from src.tasks_core import generate_task_id
    from src.tasks.local_shell import LocalShellTask, LocalShellTaskState

    task_id = generate_task_id("local_bash")
    state = LocalShellTaskState(
        id=task_id,
        type="local_bash",
        status="running",
        description="hang test",
        start_time=time.time(),
        output_file="/tmp/x",
        command="sleep 30",
        cwd="/tmp",
    )
    ctx.runtime_tasks.upsert(state)

    # ``patch.object(LocalShellTask, "kill", ...)`` patches the method
    # on the class, so the call site ``impl.kill(task_id, registry)``
    # passes ``self`` as the first argument.
    async def _hang(_self, _task_id: str, _registry) -> None:
        await asyncio.sleep(20.0)

    with patch.object(LocalShellTask, "kill", new=_hang):
        # The dispatch loop drives async tools through asyncio.run when
        # no loop is active. Bound at 7s so a real hang surfaces
        # obviously rather than via pytest-timeout.
        start = time.time()
        result = asyncio.run(TaskStopTool.call({"task_id": task_id}, ctx))
        elapsed = time.time() - start

    assert elapsed < 7.0, f"async-kill should bound at 5s, took {elapsed:.1f}s"
    assert result.is_error is True
    assert result.output["stopped"] is False
    assert result.output["task_id"] == task_id
    assert "timed out" in result.output["error"]


# ---------------------------------------------------------------------------
# task_manager dispatch — TaskStop also reaches ManagedTask entries
# ---------------------------------------------------------------------------


def test_task_manager_managed_task_is_stopped(ctx: ToolContext) -> None:
    """``context.task_manager`` is the second dispatch branch (after
    background-bash). A ManagedTask registered there must be stoppable
    via its task id."""
    import time

    def target(stop_event):
        while not stop_event.is_set():
            time.sleep(0.01)

    managed = ctx.task_manager.start(name="loop", target=target)

    result = _call_task_stop({"task_id": managed.task_id}, ctx)

    assert result.is_error is False
    assert result.output["stopped"] is True
    assert result.output["task_id"] == managed.task_id
    # The stop_event should now be set; the worker thread will exit on
    # its next tick. We don't await join() here — the unit test asserts
    # the stop signal was delivered, not the thread's exit time.
    assert managed.stop_event.is_set()


def test_task_manager_unknown_id_still_errors(ctx: ToolContext) -> None:
    """Sanity check: even with task_manager dispatch added, an unknown id
    must NOT match anything and must NOT mutate stop_requested."""
    result = _call_task_stop({"task_id": "totally-unknown-id"}, ctx)

    assert result.is_error is True
    assert "No task found" in result.output["error"]
    assert getattr(ctx, "stop_requested", False) is False


# ---------------------------------------------------------------------------
# WI-0 reason field is echoed (benign extension over TS schema)
# ---------------------------------------------------------------------------


def test_reason_is_echoed_back(ctx: ToolContext) -> None:
    result = _call_task_stop(
        {"task_id": "b-missing", "reason": "user changed plan"},
        ctx,
    )
    assert result.output["reason"] == "user changed plan"


def test_reason_defaults_to_empty_string(ctx: ToolContext) -> None:
    result = _call_task_stop({"task_id": "b-missing"}, ctx)
    assert result.output["reason"] == ""
