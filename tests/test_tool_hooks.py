"""Tests for tool hooks (pre/post tool use)."""

from __future__ import annotations

import pytest
from pathlib import Path

from src.services.tool_execution.tool_hooks import resolve_hook_permission_decision
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import AssistantMessage, create_assistant_message


def _make_tool(name: str = "TestTool") -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name=name, output="ok"),
    )


def _make_context() -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(),
    )


def _make_assistant_msg() -> AssistantMessage:
    return create_assistant_message(content="test")


class TestResolveHookPermissionDecision:
    @pytest.mark.asyncio
    async def test_no_hook_result_no_can_use_tool(self):
        tool = _make_tool()
        ctx = _make_context()
        decision = await resolve_hook_permission_decision(
            None, tool, {}, ctx, None, _make_assistant_msg(), "tu_1"
        )
        assert decision["behavior"] == "allow"

    @pytest.mark.asyncio
    async def test_hook_allow(self):
        tool = _make_tool()
        ctx = _make_context()
        hook_result = {"behavior": "allow"}
        decision = await resolve_hook_permission_decision(
            hook_result, tool, {}, ctx, None, _make_assistant_msg(), "tu_1"
        )
        assert decision["behavior"] == "allow"

    @pytest.mark.asyncio
    async def test_hook_deny(self):
        tool = _make_tool()
        ctx = _make_context()
        hook_result = {"behavior": "deny", "message": "blocked"}
        decision = await resolve_hook_permission_decision(
            hook_result, tool, {}, ctx, None, _make_assistant_msg(), "tu_1"
        )
        assert decision["behavior"] == "deny"
        assert decision["message"] == "blocked"

    @pytest.mark.asyncio
    async def test_hook_allow_with_updated_input(self):
        tool = _make_tool()
        ctx = _make_context()
        hook_result = {
            "behavior": "allow",
            "updatedInput": {"key": "new_value"},
        }
        decision = await resolve_hook_permission_decision(
            hook_result, tool, {"key": "old_value"}, ctx, None, _make_assistant_msg(), "tu_1"
        )
        assert decision["behavior"] == "allow"
        assert decision.get("input", {}).get("key") == "new_value"

    @pytest.mark.asyncio
    async def test_can_use_tool_called_when_no_hook(self):
        tool = _make_tool()
        ctx = _make_context()
        called = []

        async def mock_can_use_tool(t, inp, ctx, msg, tuid, force=None):
            called.append(True)
            return {"behavior": "allow"}

        decision = await resolve_hook_permission_decision(
            None, tool, {}, ctx, mock_can_use_tool, _make_assistant_msg(), "tu_1"
        )
        assert len(called) == 1
        assert decision["behavior"] == "allow"

    @pytest.mark.asyncio
    async def test_hook_ask_passes_to_can_use_tool(self):
        tool = _make_tool()
        ctx = _make_context()

        async def mock_can_use_tool(t, inp, ctx, msg, tuid, force=None):
            return {"behavior": "allow", "userModified": True}

        hook_result = {"behavior": "ask", "message": "Please approve"}
        decision = await resolve_hook_permission_decision(
            hook_result, tool, {}, ctx, mock_can_use_tool, _make_assistant_msg(), "tu_1"
        )
        assert decision["behavior"] == "allow"
