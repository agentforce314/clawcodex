"""WI-1.5 acceptance tests — async agents live on runtime_tasks.

Verifies ``_launch_async_agent`` registers a typed ``LocalAgentTaskState``
on ``context.runtime_tasks`` (not on the legacy ``context.tasks`` todo
dict), assigns a prefixed agent_id (``a<8>``), and the lifecycle update
through ``runtime_tasks.update`` produces ``completed`` / ``failed`` /
``killed`` terminals via the chapter-10 vocabulary.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from src.tasks.local_agent import LocalAgentTaskState, is_local_agent_task
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage


def _wait_for_terminal(
    ctx: ToolContext, task_id: str, timeout_s: float = 2.0
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = ctx.runtime_tasks.get(task_id)
        if isinstance(state, LocalAgentTaskState) and state.status in (
            "completed", "failed", "killed",
        ):
            return state.status
        time.sleep(0.02)
    state = ctx.runtime_tasks.get(task_id)
    return getattr(state, "status", "<missing>")


def test_launch_async_agent_registers_on_runtime_tasks(tmp_path: Path) -> None:
    """Headline WI-1.5 assertion: state lands on ``runtime_tasks``, NOT
    on ``context.tasks`` (no more ``metadata._internal=True`` workaround)."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "wi-1.5 smoke",
                    "prompt": "do work",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    task_id = str(result.output["agent_id"])

    # The runtime task is registered immediately on launch — it's the
    # source of truth before the lifecycle even completes.
    assert task_id in ctx.runtime_tasks
    assert is_local_agent_task(ctx.runtime_tasks.get(task_id))

    # And it does NOT live on the legacy ``context.tasks`` dict.
    assert task_id not in ctx.tasks


def test_async_agent_id_has_a_prefix(tmp_path: Path) -> None:
    """WI-1.5 prefixed-ID assertion: ``a<8 base36>`` instead of legacy
    32-char ``uuid4().hex``."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "id format",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    task_id = str(result.output["agent_id"])
    assert task_id.startswith("a"), f"missing a prefix: {task_id!r}"
    assert len(task_id) == 9, f"expected 9 chars, got {len(task_id)}: {task_id!r}"


def test_completed_lifecycle_terminal_status_is_chapter_10_vocabulary(
    tmp_path: Path,
) -> None:
    """Status flips to ``"completed"`` (chapter-10 vocab), NOT
    ``"in_progress"``/``"completed"`` from the tasks_v2 todo enum.

    The poll-for-terminal-status loop and assertions stay INSIDE the
    ``with patch`` block — ``_launch_async_agent`` schedules the
    lifecycle on a thread that may not start until after dispatch
    returns; if the patch exits first, the thread sees the real
    ``run_agent`` and reaches into the provider code.
    """
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="async done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "complete",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

        task_id = str(result.output["agent_id"])
        final = _wait_for_terminal(ctx, task_id)
        assert final == "completed"

        state = ctx.runtime_tasks.get(task_id)
        assert isinstance(state, LocalAgentTaskState)
        assert "async done" in state.result_text
        assert state.end_time is not None


def test_failed_lifecycle_records_error_on_state(tmp_path: Path) -> None:
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _failing(_params):
        if False:
            yield AssistantMessage(content=[TextBlock(text="never")])
        raise RuntimeError("boom")

    with patch("src.tool_system.tools.agent.run_agent", _failing):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "fail",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

        task_id = str(result.output["agent_id"])
        final = _wait_for_terminal(ctx, task_id)
        assert final == "failed"

        state = ctx.runtime_tasks.get(task_id)
        assert isinstance(state, LocalAgentTaskState)
        assert state.error is not None and "boom" in state.error


def test_no_internal_metadata_dict_lingers(tmp_path: Path) -> None:
    """Plan §17 Phase 1 acceptance gate: no entry in ``context.tasks``
    carries ``metadata._internal=True``. The migration is structural —
    runtime tasks live on the typed registry, not piggy-backed on the
    todo dict."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "no _internal",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

    for task_id, entry in ctx.tasks.items():
        meta = entry.get("metadata", {}) if isinstance(entry, dict) else {}
        assert meta.get("_internal") is not True, (
            f"task {task_id} still uses metadata._internal=True — migration incomplete"
        )


def test_task_output_tool_reads_from_runtime_tasks(tmp_path: Path) -> None:
    """The named WI-1.5 reader migration: TaskOutputTool now consults
    runtime_tasks first and projects the typed state into the model-facing
    output shape."""
    registry = build_default_registry(provider=object())
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    async def _fake(_params):
        yield AssistantMessage(content=[TextBlock(text="hello there")])

    with patch("src.tool_system.tools.agent.run_agent", _fake):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "read",
                    "prompt": "x",
                    "run_in_background": True,
                },
            ),
            ctx,
        )

        task_id = str(result.output["agent_id"])
        _wait_for_terminal(ctx, task_id)

        out = registry.dispatch(
            ToolCall(name="TaskOutput", input={"task_id": task_id}),
            ctx,
        )
        assert out.output["retrieval_status"] == "success"
        assert out.output["task"]["task_type"] == "local_agent"
        assert out.output["task"]["status"] == "completed"
        assert "hello there" in out.output["task"]["output"]
