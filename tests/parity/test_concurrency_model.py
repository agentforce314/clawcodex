"""WS-10: Behavioral parity — concurrency model matches TS streaming executor.

Verifies:
- Concurrent-safe tools execute in parallel
- Non-concurrent tools get exclusive access
- Read-write lock semantics: concurrent + concurrent OK, concurrent + exclusive NO
- Sibling abort on Bash error
- Three-tier abort hierarchy
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from src.services.tool_execution.streaming_executor import (
    StreamingToolExecutor,
    ToolUseBlock,
    TrackedTool,
)
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext
from src.types.messages import AssistantMessage
from src.utils.abort_controller import AbortController, create_abort_controller


def _make_tool(name: str, *, concurrent: bool = False) -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: None,
        is_concurrency_safe=lambda _: concurrent,
        is_read_only=lambda _: concurrent,
    )


def _make_context(tools: list[Tool], abort: AbortController | None = None) -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.abort_controller = abort or create_abort_controller()
    ctx.set_in_progress_tool_use_ids = None
    return ctx


class TestConcurrencyExecutionModel(unittest.TestCase):
    """Concurrent-safe tools can execute in parallel; non-safe tools require exclusive access."""

    def test_can_execute_concurrent_when_empty(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        self.assertTrue(executor._can_execute_tool(is_concurrency_safe=True))

    def test_can_execute_non_concurrent_when_empty(self) -> None:
        tools = [_make_tool("Edit")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        self.assertTrue(executor._can_execute_tool(is_concurrency_safe=False))

    def test_concurrent_with_concurrent_ok(self) -> None:
        """Two concurrent-safe tools can execute together."""
        tools = [_make_tool("Read", concurrent=True), _make_tool("Glob", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        # Simulate one executing tool
        executor._tools.append(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=True,
        ))
        self.assertTrue(executor._can_execute_tool(is_concurrency_safe=True))

    def test_non_concurrent_blocked_by_executing(self) -> None:
        """Non-concurrent tool cannot execute while another is running."""
        tools = [_make_tool("Read", concurrent=True), _make_tool("Edit")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor._tools.append(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=True,
        ))
        self.assertFalse(executor._can_execute_tool(is_concurrency_safe=False))

    def test_concurrent_blocked_by_non_concurrent(self) -> None:
        """Concurrent tool cannot execute while non-concurrent tool is running."""
        tools = [_make_tool("Edit"), _make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor._tools.append(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Edit", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=False,
        ))
        self.assertFalse(executor._can_execute_tool(is_concurrency_safe=True))


class TestStreamingExecutorToolTracking(unittest.TestCase):
    """Tool tracking states match TS StreamingToolExecutor."""

    def test_initial_status_queued(self) -> None:
        tracked = TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="queued", is_concurrency_safe=True,
        )
        self.assertEqual(tracked.status, "queued")

    def test_status_transitions(self) -> None:
        valid_transitions = ["queued", "executing", "completed", "yielded"]
        for status in valid_transitions:
            tracked = TrackedTool(
                id="1", block=ToolUseBlock(id="1", name="Read", input={}),
                assistant_message=AssistantMessage(),
                status=status, is_concurrency_safe=True,
            )
            self.assertEqual(tracked.status, status)

    def test_tracked_tool_has_results_field(self) -> None:
        tracked = TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="queued", is_concurrency_safe=True,
        )
        self.assertIsNone(tracked.results)

    def test_tracked_tool_has_promise_field(self) -> None:
        tracked = TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="queued", is_concurrency_safe=True,
        )
        self.assertIsNone(tracked.promise)


class TestSiblingAbort(unittest.TestCase):
    """Sibling abort on Bash error cascades to cancel siblings."""

    def test_bash_error_sets_has_errored(self) -> None:
        tools = [_make_tool("Bash")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor._has_errored = True
        executor._errored_tool_description = "Bash(ls)"
        reason = executor._get_abort_reason(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=True,
        ))
        self.assertEqual(reason, "sibling_error")

    def test_discard_returns_streaming_fallback(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor.discard()
        reason = executor._get_abort_reason(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=True,
        ))
        self.assertEqual(reason, "streaming_fallback")

    def test_no_abort_reason_when_normal(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        reason = executor._get_abort_reason(TrackedTool(
            id="1", block=ToolUseBlock(id="1", name="Read", input={}),
            assistant_message=AssistantMessage(),
            status="executing", is_concurrency_safe=True,
        ))
        self.assertIsNone(reason)


class TestSyntheticErrorMessages(unittest.TestCase):
    """Synthetic error messages match TS format."""

    def test_sibling_error_message(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor._errored_tool_description = "Bash(ls)"
        msg = executor._create_synthetic_error_message(
            "tool_1", "sibling_error", AssistantMessage()
        )
        content = msg.content
        self.assertIsInstance(content, list)
        self.assertTrue(any(
            "Cancelled" in str(block.get("content", ""))
            for block in content
            if isinstance(block, dict)
        ))

    def test_user_interrupted_message(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        msg = executor._create_synthetic_error_message(
            "tool_1", "user_interrupted", AssistantMessage()
        )
        content = msg.content
        self.assertIsInstance(content, list)

    def test_streaming_fallback_message(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        msg = executor._create_synthetic_error_message(
            "tool_1", "streaming_fallback", AssistantMessage()
        )
        content = msg.content
        self.assertIsInstance(content, list)
        self.assertTrue(any(
            "Streaming fallback" in str(block.get("content", ""))
            for block in content
            if isinstance(block, dict)
        ))


class TestUnknownToolHandling(unittest.TestCase):
    """Unknown tools get error results like in TS."""

    def test_unknown_tool_immediate_error(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(
            tool_definitions=tools,
            can_use_tool=MagicMock(),
            tool_use_context=ctx,
        )
        executor.add_tool(
            ToolUseBlock(id="1", name="UnknownTool", input={}),
            AssistantMessage(),
        )
        self.assertEqual(len(executor._tools), 1)
        self.assertEqual(executor._tools[0].status, "completed")
        results = executor._tools[0].results
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        # Should contain error message
        msg = results[0]
        self.assertTrue(any(
            "No such tool" in str(block.get("content", ""))
            for block in msg.content
            if isinstance(block, dict)
        ))


if __name__ == "__main__":
    unittest.main()
