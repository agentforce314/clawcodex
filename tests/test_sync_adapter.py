"""Tests for src/services/tool_execution/sync_adapter.py.

Verifies the sync materialization adapter correctly bridges
``run_tool_use``'s async-generator output to a single
``ToolDispatchResult`` consumable by sync callers.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.permissions.types import (
    PermissionAllowDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    ToolPermissionContext,
)
from src.services.tool_execution.sync_adapter import (
    ToolDispatchResult,
    dispatch_full,
    make_stub_assistant_message,
)
from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolCall, ToolResult


def _make_context() -> ToolContext:
    tmp = tempfile.mkdtemp()
    ctx = ToolContext(
        workspace_root=Path(tmp),
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )
    return ctx


def _make_tool(
    name: str = "Echo",
    output: Any = None,
    new_messages: list[Any] | None = None,
    context_modifier: Any = None,
    is_concurrency_safe: bool = False,
    check_permissions: Any = None,
) -> Tool:
    def _call(_inp: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        return ToolResult(
            name=name,
            output=output if output is not None else {"ok": True},
            new_messages=new_messages,
            context_modifier=context_modifier,
        )
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=_call,
        is_concurrency_safe=lambda _i: is_concurrency_safe,
        check_permissions=check_permissions,
    )


class TestDispatchFullSimple(unittest.TestCase):
    """Happy-path: tool returns a dict, no auxiliary messages."""

    def test_simple_dict_output(self) -> None:
        ctx = _make_context()
        tool = _make_tool("Echo", output={"hello": "world"})
        call = ToolCall(name="Echo", input={}, tool_use_id="tu-1")
        msg = make_stub_assistant_message()
        result = dispatch_full(call, ctx, msg, tools=[tool])

        self.assertIsInstance(result, ToolDispatchResult)
        self.assertFalse(result.is_error)
        self.assertEqual(result.output, {"hello": "world"})
        self.assertEqual(result.tool_result_block["tool_use_id"], "tu-1")
        self.assertEqual(result.tool_result_block["type"], "tool_result")
        self.assertEqual(result.new_messages, [])
        self.assertIsNone(result.context_modifier)

    def test_string_output_preserved(self) -> None:
        ctx = _make_context()
        tool = _make_tool("Echo", output="raw string output")
        call = ToolCall(name="Echo", input={}, tool_use_id="tu-2")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])
        # output is the raw ToolResult.data, preserved as a string
        self.assertEqual(result.output, "raw string output")


class TestDispatchFullToolsParameter(unittest.TestCase):
    """``tools=`` kwarg overrides ``context.options.tools`` for lookup."""

    def test_explicit_tools_used(self) -> None:
        ctx = _make_context()
        wrong_tool = _make_tool("WrongTool", output="wrong")
        right_tool = _make_tool("RightTool", output="right")
        ctx.options = ToolUseOptions(tools=[wrong_tool])
        call = ToolCall(name="RightTool", input={}, tool_use_id="tu-x")
        result = dispatch_full(call, ctx, make_stub_assistant_message(),
                                tools=[right_tool])
        self.assertFalse(result.is_error)
        self.assertEqual(result.output, "right")

    def test_unknown_tool_returns_error(self) -> None:
        ctx = _make_context()
        call = ToolCall(name="DoesNotExist", input={}, tool_use_id="tu-?")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[])
        self.assertTrue(result.is_error)
        self.assertIn("No such tool", str(result.tool_result_block.get("content", "")))


class TestDispatchFullNewMessages(unittest.TestCase):
    """``ToolResult.new_messages`` surfaces in ``result.new_messages``,
    NOT in the primary tool_result block."""

    def test_new_messages_propagated(self) -> None:
        from src.types.messages import create_user_message

        extra1 = create_user_message(content=[{"type": "text", "text": "extra one"}])
        extra2 = create_user_message(content=[{"type": "text", "text": "extra two"}])

        ctx = _make_context()
        tool = _make_tool("AgentLike", output={"transcript": "..."}, new_messages=[extra1, extra2])
        call = ToolCall(name="AgentLike", input={}, tool_use_id="tu-agent")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])

        self.assertFalse(result.is_error)
        # Primary result still works
        self.assertEqual(result.tool_result_block["tool_use_id"], "tu-agent")
        # Both extras land in new_messages
        self.assertEqual(len(result.new_messages), 2)


class TestDispatchFullContextModifier(unittest.TestCase):
    """``ToolResult.context_modifier`` surfaces in ``result.context_modifier``."""

    def test_context_modifier_propagated(self) -> None:
        def _modifier(c: ToolContext) -> ToolContext:
            c.plan_mode = True
            return c

        ctx = _make_context()
        tool = _make_tool("PlanLike", output={"ok": True}, context_modifier=_modifier)
        call = ToolCall(name="PlanLike", input={}, tool_use_id="tu-plan")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])

        self.assertIsNotNone(result.context_modifier)
        self.assertFalse(ctx.plan_mode)  # not yet applied
        # Caller applies the modifier
        result.context_modifier(ctx)
        self.assertTrue(ctx.plan_mode)


class TestDispatchFullPermissionDenied(unittest.TestCase):
    """Permission denial surfaces as ``is_error=True``, no new_messages."""

    def test_deny_returns_is_error(self) -> None:
        def _deny(_inp: dict[str, Any], _ctx: Any):
            return PermissionDenyDecision(
                behavior="deny",
                message="No way",
            )

        ctx = _make_context()
        ctx.permission_context = ToolPermissionContext(mode="default")
        tool = _make_tool("Blocked", output={"never": "called"}, check_permissions=_deny)
        call = ToolCall(name="Blocked", input={}, tool_use_id="tu-deny")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])

        self.assertTrue(result.is_error)
        self.assertEqual(result.new_messages, [])
        # Output should be the error string from the tool_result block
        # (toolUseResult on deny path carries the formatted error)
        self.assertIn("No way", str(result.tool_result_block.get("content", "")))


class TestDispatchFullAskPermission(unittest.TestCase):
    """Permission ``ask`` is resolved interactively via
    ``permission_handler`` (mirrors ``ToolRegistry.dispatch`` semantics)."""

    def test_ask_with_handler_allow(self) -> None:
        from src.permissions.types import PermissionAskDecision

        def _ask(_inp: dict[str, Any], _ctx: Any):
            return PermissionAskDecision(behavior="ask", message="confirm?")

        ctx = _make_context()
        ctx.permission_context = ToolPermissionContext(mode="default")
        ctx.permission_handler = lambda name, msg, sug: (True, False)
        tool = _make_tool("NeedApproval", output="done", check_permissions=_ask)
        call = ToolCall(name="NeedApproval", input={}, tool_use_id="tu-ask-y")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])
        self.assertFalse(result.is_error)
        self.assertEqual(result.output, "done")

    def test_ask_with_handler_deny(self) -> None:
        from src.permissions.types import PermissionAskDecision

        def _ask(_inp: dict[str, Any], _ctx: Any):
            return PermissionAskDecision(behavior="ask", message="confirm?")

        ctx = _make_context()
        ctx.permission_context = ToolPermissionContext(mode="default")
        ctx.permission_handler = lambda name, msg, sug: (False, False)
        tool = _make_tool("NeedApproval", output="done", check_permissions=_ask)
        call = ToolCall(name="NeedApproval", input={}, tool_use_id="tu-ask-n")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])
        self.assertTrue(result.is_error)

    def test_ask_without_handler_denies(self) -> None:
        """Without a permission_handler, ask resolves to deny (parity
        with ToolRegistry.dispatch behavior)."""
        from src.permissions.types import PermissionAskDecision

        def _ask(_inp: dict[str, Any], _ctx: Any):
            return PermissionAskDecision(behavior="ask", message="confirm?")

        ctx = _make_context()
        ctx.permission_context = ToolPermissionContext(mode="default")
        tool = _make_tool("NeedApproval", output="done", check_permissions=_ask)
        call = ToolCall(name="NeedApproval", input={}, tool_use_id="tu-ask-?")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])
        self.assertTrue(result.is_error)


class TestDispatchFullErrorClassification(unittest.TestCase):
    """Exceptions inside tool.call are caught + classified by the pipeline."""

    def test_tool_exception_is_classified(self) -> None:
        def _broken(_inp: dict[str, Any], _ctx: ToolContext) -> ToolResult:
            raise ValueError("kaboom")

        tool = build_tool(
            name="Broken",
            input_schema={"type": "object", "properties": {}},
            call=_broken,
        )
        ctx = _make_context()
        call = ToolCall(name="Broken", input={}, tool_use_id="tu-err")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])

        self.assertTrue(result.is_error)
        # The pipeline wraps the error in <tool_use_error>...
        content = str(result.tool_result_block.get("content", ""))
        self.assertIn("kaboom", content)


class TestDispatchFullToolUseIdMatching(unittest.TestCase):
    """The primary result block is matched by tool_use_id."""

    def test_tool_use_id_propagated(self) -> None:
        ctx = _make_context()
        tool = _make_tool("Echo", output={"a": 1})
        call = ToolCall(name="Echo", input={}, tool_use_id="unique-id-123")
        result = dispatch_full(call, ctx, make_stub_assistant_message(), tools=[tool])
        self.assertEqual(result.tool_result_block["tool_use_id"], "unique-id-123")


if __name__ == "__main__":
    unittest.main()
