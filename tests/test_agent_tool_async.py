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
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = str(context.tasks.get(task_id, {}).get("status", ""))
        if status and status != "in_progress":
            return status
        time.sleep(0.05)
    return str(context.tasks.get(task_id, {}).get("status", ""))


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
        assert task_id in context.tasks

        final_status = _wait_for_task_status(context, task_id)
        assert final_status == "completed"
        assert "async done" in str(context.tasks[task_id].get("output", ""))


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
        assert "boom" in str(context.tasks[task_id].get("output", ""))
