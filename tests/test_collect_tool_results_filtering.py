"""ch07 / Phase 2d (T-attach): `_collect_tool_results` adapter filtering.

Pins the adapter's filter contract directly so a regression doesn't
require waiting for the indirect coverage in the parity / hook tests.
The adapter:
- Skips ``MessageUpdate(message=None)`` (flush-only yields after
  concurrent batches).
- Drops ``AttachmentMessage`` (post-hook attachments — not surfaced today).
- Drops ``SystemMessage`` (side-channel).
- Appends ``UserMessage`` (the legacy tool_result shape).
- Threads ``new_context`` updates through to the returned context.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.query.query import _collect_tool_results
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.types.messages import (
    AssistantMessage,
    AttachmentMessage,
    SystemMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
)
from src.utils.abort_controller import AbortController


def _make_tool() -> Tool:
    return build_tool(
        name="Tool",
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name="Tool", output="ok"),
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )


def _make_ctx(tools: list[Tool]) -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools),
        abort_controller=AbortController(),
    )


async def _make_run_tools_yields(*updates):
    """Async generator that yields the given pre-built MessageUpdates."""
    for u in updates:
        yield u


class TestCollectToolResultsFiltering(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_appended(self):
        """A plain UserMessage from the orchestrator lands in results."""
        from src.services.tool_execution.orchestrator import MessageUpdate
        tool = _make_tool()
        ctx = _make_ctx([tool])

        um = create_user_message(content=[{
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "ok",
            "is_error": False,
        }])

        async def fake_run_tools(*args, **kwargs):
            yield MessageUpdate(message=um, new_context=ctx)

        with patch("src.services.tool_execution.orchestrator.run_tools", new=fake_run_tools):
            results, _ = await _collect_tool_results(
                [ToolUseBlock(id="t1", name="Tool", input={})],
                [create_assistant_message(content="x")],
                ToolRegistry([tool]), ctx, [tool],
            )
        self.assertEqual(len(results), 1)
        # The result is normalized to a ToolResultBlock
        content = results[0].content
        self.assertTrue(any(hasattr(b, "tool_use_id") for b in content))

    async def test_message_none_skipped(self):
        """`MessageUpdate(message=None, new_context=...)` is a flush yield —
        no entry in results but the context update is captured."""
        from src.services.tool_execution.orchestrator import MessageUpdate
        tool = _make_tool()
        ctx = _make_ctx([tool])
        new_ctx = _make_ctx([tool])  # distinct sentinel

        async def fake_run_tools(*args, **kwargs):
            yield MessageUpdate(message=None, new_context=new_ctx)

        with patch("src.services.tool_execution.orchestrator.run_tools", new=fake_run_tools):
            results, last_ctx = await _collect_tool_results(
                [], [], ToolRegistry([tool]), ctx, [tool],
            )
        self.assertEqual(len(results), 0)
        self.assertIs(last_ctx, new_ctx)

    async def test_attachment_message_dropped(self):
        """AttachmentMessage (subclass of UserMessage) is filtered out."""
        from src.services.tool_execution.orchestrator import MessageUpdate
        tool = _make_tool()
        ctx = _make_ctx([tool])

        attach = AttachmentMessage(attachments=[{"type": "diagnostic", "value": "x"}])

        async def fake_run_tools(*args, **kwargs):
            yield MessageUpdate(message=attach, new_context=ctx)

        with patch("src.services.tool_execution.orchestrator.run_tools", new=fake_run_tools):
            results, _ = await _collect_tool_results(
                [], [], ToolRegistry([tool]), ctx, [tool],
            )
        self.assertEqual(len(results), 0, "AttachmentMessage must not leak into results")

    async def test_system_message_dropped(self):
        """SystemMessage / progress side-channel messages are dropped."""
        from src.services.tool_execution.orchestrator import MessageUpdate
        tool = _make_tool()
        ctx = _make_ctx([tool])

        sys = SystemMessage(content="progress", subtype="tool_use_progress")

        async def fake_run_tools(*args, **kwargs):
            yield MessageUpdate(message=sys, new_context=ctx)

        with patch("src.services.tool_execution.orchestrator.run_tools", new=fake_run_tools):
            results, _ = await _collect_tool_results(
                [], [], ToolRegistry([tool]), ctx, [tool],
            )
        self.assertEqual(len(results), 0)

    async def test_result_count_matches_tool_block_count(self):
        """For N concurrent-safe tools that each emit a tool_result,
        the adapter returns exactly N UserMessages."""
        tool = _make_tool()
        ctx = _make_ctx([tool])
        registry = ToolRegistry([tool])

        blocks = [ToolUseBlock(id=f"t{i}", name="Tool", input={}) for i in range(5)]
        results, _ = await _collect_tool_results(
            blocks, [create_assistant_message(content="x")], registry, ctx, [tool],
        )
        # Each tool produced one tool_result UserMessage. (run_tools may
        # yield a final flush-only MessageUpdate(new_context=...) after
        # the concurrent batch, but the adapter drops those.)
        self.assertEqual(len(results), 5)


if __name__ == "__main__":
    unittest.main()
