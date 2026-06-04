from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage


def _wait_for_task_status(context: ToolContext, task_id: str, timeout_s: float = 2.0) -> str:
    """Poll the chapter-10 runtime_tasks registry for terminal status.

    Pre-Chunk-B this checked ``context.tasks[task_id].status`` (the dict
    that hosted async-agent state via the ``metadata._internal=True``
    workaround). Post-WI-1.5 the source of truth is
    ``context.runtime_tasks``; the legacy dict no longer holds agent state.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = context.runtime_tasks.get(task_id)
        if state is not None:
            status = str(state.status)
            # ``running`` is the chapter-10 in-flight value. ``completed``
            # / ``failed`` / ``killed`` are terminal — return the moment
            # we see one.
            if status and status != "running" and status != "pending":
                return status
        time.sleep(0.05)
    state = context.runtime_tasks.get(task_id)
    return str(state.status) if state is not None else ""


def _task_output_text(context: ToolContext, task_id: str) -> str:
    """Helper: return the agent's final text output post-Chunk-B."""
    from src.tasks.local_agent import LocalAgentTaskState

    state = context.runtime_tasks.get(task_id)
    if isinstance(state, LocalAgentTaskState):
        return state.result_text
    return ""


def test_async_agent_launch_persists_completed_output(tmp_path: Path) -> None:
    registry = build_default_registry(provider=object())
    context = ToolContext(workspace_root=tmp_path)

    async def _fake_run_agent(_params):
        yield AssistantMessage(content=[TextBlock(text="async done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "background test",
                    "prompt": "do background work",
                    "run_in_background": True,
                },
            ),
            context,
        )

        assert result.is_error is False
        assert isinstance(result.output, dict)
        assert result.output.get("status") == "async_launched"

        task_id = str(result.output["agent_id"])
        assert result.output.get("task_output_key") == task_id
        # Chapter-10 / Chunk B / WI-1.5: the runtime task lives on
        # ``context.runtime_tasks`` (typed), no longer on ``context.tasks``.
        assert task_id in context.runtime_tasks
        assert task_id not in context.tasks  # explicit migration assertion

        final_status = _wait_for_task_status(context, task_id)
        assert final_status == "completed"
        assert "async done" in _task_output_text(context, task_id)


def test_async_agent_launch_marks_failed_output(tmp_path: Path) -> None:
    registry = build_default_registry(provider=object())
    context = ToolContext(workspace_root=tmp_path)

    async def _failing_run_agent(_params):
        if False:
            yield AssistantMessage(content=[TextBlock(text="unused")])
        raise RuntimeError("boom")

    with patch("src.tool_system.tools.agent.run_agent", _failing_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "background failure",
                    "prompt": "fail",
                    "run_in_background": True,
                },
            ),
            context,
        )

        task_id = str(result.output["agent_id"])
        final_status = _wait_for_task_status(context, task_id)
        assert final_status == "failed"
        assert "boom" in _task_output_text(context, task_id)
