"""Tests for StreamingToolExecutor."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.services.tool_execution.streaming_executor import (
    StreamingToolExecutor,
    ToolUseBlock,
    MessageUpdate,
)
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.types.messages import AssistantMessage, create_assistant_message
from src.utils.abort_controller import AbortController


def _make_tool(name: str, concurrency_safe: bool = False) -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name=name, output=f"ok:{name}"),
        is_concurrency_safe=lambda _inp: concurrency_safe,
    )


def _make_context(tools: list[Tool] | None = None) -> ToolContext:
    from src.tool_system.context import ToolUseOptions
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools or []),
        abort_controller=AbortController(),
    )


def _make_block(name: str, tool_id: str = "tu_1") -> ToolUseBlock:
    return ToolUseBlock(id=tool_id, name=name, input={})


def _make_assistant_msg() -> AssistantMessage:
    return create_assistant_message(content="test")


class TestStreamingExecutorBasic:
    def test_create_executor(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        assert executor is not None

    def test_add_unknown_tool(self):
        tools = [_make_tool("known")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        executor.add_tool(_make_block("unknown"), _make_assistant_msg())
        results = list(executor.get_completed_results())
        assert len(results) == 1
        assert results[0].message is not None

    def test_discard(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        executor.discard()
        results = list(executor.get_completed_results())
        assert len(results) == 0


class TestConcurrencyModel:
    def test_can_execute_concurrent_safe(self):
        tools = [_make_tool("t1", concurrency_safe=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        assert executor._can_execute_tool(True)

    def test_cannot_execute_non_concurrent_while_executing(self):
        tools = [_make_tool("t1", concurrency_safe=True)]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        from src.services.tool_execution.streaming_executor import TrackedTool
        executor._tools.append(TrackedTool(
            id="x", block=_make_block("t1"), assistant_message=_make_assistant_msg(),
            status="executing", is_concurrency_safe=True,
        ))
        assert not executor._can_execute_tool(False)
        assert executor._can_execute_tool(True)


class TestSiblingAbort:
    def test_has_errored_flag(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        assert not executor._has_errored
        executor._has_errored = True
        executor._errored_tool_description = "Bash(ls)"
        result = executor._get_abort_reason(
            MagicMock(block=MagicMock(name_="t"))
        )
        assert result == "sibling_error"


class TestSyntheticErrors:
    def test_sibling_error_message(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        executor._errored_tool_description = "Bash(ls)"
        msg = executor._create_synthetic_error_message(
            "tu_1", "sibling_error", _make_assistant_msg()
        )
        assert msg is not None

    def test_user_interrupted_message(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        msg = executor._create_synthetic_error_message(
            "tu_1", "user_interrupted", _make_assistant_msg()
        )
        assert msg is not None

    def test_streaming_fallback_message(self):
        tools = [_make_tool("test")]
        ctx = _make_context(tools)
        executor = StreamingToolExecutor(tools, None, ctx)
        msg = executor._create_synthetic_error_message(
            "tu_1", "streaming_fallback", _make_assistant_msg()
        )
        assert msg is not None
