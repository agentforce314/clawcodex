"""Step 1 — concurrent _execute_tool must not share an abort_controller.

Reproduces G6 from the ch07 gap analysis: prior code mutated the
executor's stored ``self._tool_use_context.abort_controller`` for the
duration of one tool's execution and restored it in ``finally``. With
two concurrent-safe tools running in parallel, tool A's abort signal
could be observed by tool B's ``run_tool_use`` mid-flight, breaking
sibling abort isolation.

The fix copies the context per tool via ``dataclasses.replace`` so each
tool sees its own controller. These tests verify that:

1. Two concurrent tools see *distinct* per-tool controllers.
2. The executor's own ``self._tool_use_context.abort_controller`` is
   never mutated during execution.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.services.tool_execution.streaming_executor import (
    StreamingToolExecutor,
    ToolUseBlock,
)
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import create_assistant_message
from src.utils.abort_controller import AbortController


def _capture_tool(name: str, captured: list, *, concurrent: bool = True) -> Tool:
    """A tool whose ``call`` records the abort_controller it received."""

    def _call(_inp, ctx):
        captured.append(ctx.abort_controller)
        return ToolResult(name=name, output=f"ok:{name}")

    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=_call,
        is_concurrency_safe=lambda _: concurrent,
        is_read_only=lambda _: concurrent,
    )


def _make_context(tools: list[Tool], abort: AbortController) -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools),
        abort_controller=abort,
    )


def _allow_all(_tool, tool_input, _ctx, _msg, _tool_use_id):
    return {"behavior": "allow", "updatedInput": tool_input}


class TestPerToolAbortControllerIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_each_tool_gets_its_own_abort_controller(self):
        captured: list = []
        tool = _capture_tool("Read", captured)
        parent = AbortController()
        ctx = _make_context([tool], parent)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        msg = create_assistant_message(content="hi")
        executor.add_tool(ToolUseBlock(id="t1", name="Read", input={}), msg)
        executor.add_tool(ToolUseBlock(id="t2", name="Read", input={}), msg)

        async for _ in executor.get_remaining_results():
            pass

        # Both tools observed an abort_controller; the per-tool copies
        # must be distinct objects.
        self.assertEqual(len(captured), 2)
        self.assertIsNot(captured[0], captured[1])
        self.assertIsNot(captured[0], ctx.abort_controller)
        self.assertIsNot(captured[1], ctx.abort_controller)

    async def test_executor_context_abort_controller_unchanged(self):
        captured: list = []
        tool = _capture_tool("Read", captured)
        parent = AbortController()
        ctx = _make_context([tool], parent)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        msg = create_assistant_message(content="hi")
        executor.add_tool(ToolUseBlock(id="t1", name="Read", input={}), msg)

        async for _ in executor.get_remaining_results():
            pass

        # The executor never mutated ctx.abort_controller — the parent
        # the caller handed in is still in place.
        self.assertIs(ctx.abort_controller, parent)


if __name__ == "__main__":
    unittest.main()
