"""ch07 / Phase 2e: PreToolUse hooks fire on the concurrent batch path.

The production query loop used to dispatch tools through
`_run_tools_partitioned` → `tool_registry.dispatch()`, which bypassed
PreToolUse/PostToolUse hooks entirely. After Phase 2e the path goes
through `_collect_tool_results` → `orchestrator.run_tools()` →
`run_tool_use`, which honours hooks. This test pins the success
criterion for C3.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from src.query.query import _collect_tool_results
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.types.messages import AssistantMessage, create_assistant_message
from src.utils.abort_controller import AbortController


def _make_tool(name: str = "Reader") -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name=name, output="ok"),
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )


def _make_context(tools: list[Tool]) -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools),
        abort_controller=AbortController(),
    )


class TestPreToolUseHookOnConcurrentBatch(unittest.IsolatedAsyncioTestCase):
    async def test_pre_tool_use_hook_deny_short_circuits_dispatch(self):
        """A PreToolUse hook returning behavior='deny' must prevent
        the tool from running and surface an error tool_result."""
        tool_ran = []

        def _call(inp, ctx):
            tool_ran.append(inp)
            return ToolResult(name="Reader", output="should-not-run")

        tool = build_tool(
            name="Reader",
            input_schema={"type": "object", "properties": {}},
            call=_call,
            is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
        )
        ctx = _make_context([tool])
        registry = ToolRegistry([tool])
        msg = create_assistant_message(content="x")

        async def fake_pre_hook(tool_use_context, tool_arg, processed_input, tool_use_id):
            # Yield a single deny decision in the shape the resolver expects.
            yield {
                "type": "hookPermissionResult",
                "hookPermissionResult": {
                    "behavior": "deny",
                    "message": "Blocked by test hook",
                },
            }

        # Patch the hook entry point. `run_pre_tool_use_hooks` is
        # imported inside `run_tool_use`, so we patch where it lives.
        with patch(
            "src.services.tool_execution.tool_hooks.run_pre_tool_use_hooks",
            new=fake_pre_hook,
        ):
            results, _ = await _collect_tool_results(
                [ToolUseBlock(id="r1", name="Reader", input={"x": 1})],
                [msg],
                registry,
                ctx,
                [tool],
            )

        # Tool MUST NOT have run.
        self.assertEqual(
            tool_ran, [],
            "PreToolUse hook returned deny but the tool ran anyway — hook bypass regressed",
        )
        # Error tool_result must have been emitted. After Phase 2e's
        # `_normalize_tool_result_blocks` the block is a
        # ``ToolResultBlock`` dataclass, not a dict.
        self.assertEqual(len(results), 1)
        content = results[0].content
        self.assertIsInstance(content, list)
        error_blocks = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                error_blocks.append({"content": b.get("content", "")})
            elif hasattr(b, "is_error") and getattr(b, "is_error", False):
                error_blocks.append({"content": getattr(b, "content", "")})
        self.assertTrue(
            error_blocks,
            f"no error tool_result emitted for hook-denied tool; got content={content!r}",
        )
        self.assertIn("Blocked by test hook", str(error_blocks[0]["content"]))


if __name__ == "__main__":
    unittest.main()
