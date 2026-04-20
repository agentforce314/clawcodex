from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.permissions.types import ToolPermissionContext
from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import (
    ToolRegistry,
    assemble_tool_pool,
    filter_tools_by_deny_rules,
    get_all_base_tools,
    get_merged_tools,
    get_tools,
)


def _noop_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(name="Noop", output={"ok": True})


def _make_tool(name: str, **kwargs: Any) -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=_noop_call,
        **kwargs,
    )


class TestToolRegistry(unittest.TestCase):
    def test_register_and_get(self) -> None:
        reg = ToolRegistry()
        t = _make_tool("Alpha")
        reg.register(t)
        self.assertIs(reg.get("Alpha"), t)
        self.assertIs(reg.get("alpha"), t)

    def test_get_missing_returns_none(self) -> None:
        reg = ToolRegistry()
        self.assertIsNone(reg.get("Missing"))

    def test_duplicate_name_raises(self) -> None:
        reg = ToolRegistry()
        reg.register(_make_tool("X"))
        with self.assertRaises(ValueError):
            reg.register(_make_tool("X"))

    def test_duplicate_alias_raises(self) -> None:
        reg = ToolRegistry()
        reg.register(_make_tool("A", aliases=("shared",)))
        with self.assertRaises(ValueError):
            reg.register(_make_tool("B", aliases=("shared",)))

    def test_alias_lookup(self) -> None:
        reg = ToolRegistry()
        t = _make_tool("Main", aliases=("Alt",))
        reg.register(t)
        self.assertIs(reg.get("Alt"), t)
        self.assertIs(reg.get("alt"), t)

    def test_list_tools(self) -> None:
        reg = ToolRegistry()
        reg.register(_make_tool("A"))
        reg.register(_make_tool("B"))
        names = [t.name for t in reg.list_tools()]
        self.assertEqual(names, ["A", "B"])

    def test_init_with_tools(self) -> None:
        tools = [_make_tool("X"), _make_tool("Y")]
        reg = ToolRegistry(tools=tools)
        self.assertEqual(len(reg.list_tools()), 2)
        self.assertIs(reg.get("X"), tools[0])


class TestRegistryDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = ToolContext(workspace_root=Path(self.tmp.name))
        self.reg = ToolRegistry()
        self.tool = _make_tool("Echo")
        self.reg.register(self.tool)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dispatch_success(self) -> None:
        result = self.reg.dispatch(ToolCall(name="Echo", input={}), self.ctx)
        self.assertFalse(result.is_error)
        self.assertTrue(result.output["ok"])

    def test_dispatch_unknown_tool(self) -> None:
        result = self.reg.dispatch(ToolCall(name="Unknown", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertIn("unknown tool", result.output["error"])

    def test_dispatch_sets_tool_use_id(self) -> None:
        result = self.reg.dispatch(
            ToolCall(name="Echo", input={}, tool_use_id="id123"),
            self.ctx,
        )
        self.assertEqual(result.tool_use_id, "id123")

    def test_dispatch_validates_input(self) -> None:
        from src.tool_system.build_tool import ValidationResult

        def _validate(inp: dict, ctx: ToolContext) -> ValidationResult:
            if "x" not in inp:
                return ValidationResult.fail("x required")
            return ValidationResult.ok()

        t = _make_tool("V", validate_input=_validate)
        reg = ToolRegistry([t])
        result = reg.dispatch(ToolCall(name="V", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertIn("x required", result.output["error"])

    def test_dispatch_checks_permissions_deny(self) -> None:
        from src.permissions.types import PermissionDenyDecision

        def _deny(inp: dict, ctx: ToolContext):
            return PermissionDenyDecision(message="nope")

        t = _make_tool("Blocked", check_permissions=_deny)
        reg = ToolRegistry([t])
        result = reg.dispatch(ToolCall(name="Blocked", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertIn("nope", result.output["error"])

    def test_dispatch_checks_permissions_ask_without_handler(self) -> None:
        from src.permissions.types import PermissionAskDecision

        def _ask(inp: dict, ctx: ToolContext):
            return PermissionAskDecision(message="confirm?")

        t = _make_tool("NeedApproval", check_permissions=_ask)
        reg = ToolRegistry([t])
        ctx = ToolContext(
            workspace_root=Path(self.tmp.name),
            permission_context=ToolPermissionContext(mode="default"),
        )
        result = reg.dispatch(ToolCall(name="NeedApproval", input={}), ctx)
        self.assertTrue(result.is_error)

    def test_dispatch_checks_permissions_ask_with_handler_allow(self) -> None:
        from src.permissions.types import PermissionAskDecision

        def _ask(inp: dict, ctx: ToolContext):
            return PermissionAskDecision(message="confirm?")

        t = _make_tool("NeedApproval", check_permissions=_ask)
        reg = ToolRegistry([t])
        self.ctx.permission_handler = lambda name, msg, sug: (True, False)
        result = reg.dispatch(ToolCall(name="NeedApproval", input={}), self.ctx)
        self.assertFalse(result.is_error)

    def test_dispatch_checks_permissions_ask_with_handler_deny(self) -> None:
        from src.permissions.types import PermissionAskDecision

        def _ask(inp: dict, ctx: ToolContext):
            return PermissionAskDecision(message="confirm?")

        t = _make_tool("NeedApproval", check_permissions=_ask)
        reg = ToolRegistry([t])
        ctx = ToolContext(
            workspace_root=Path(self.tmp.name),
            permission_context=ToolPermissionContext(mode="default"),
        )
        ctx.permission_handler = lambda name, msg, sug: (False, False)
        result = reg.dispatch(ToolCall(name="NeedApproval", input={}), ctx)
        self.assertTrue(result.is_error)
        self.assertIn("denied", result.output["error"])


class TestPipelineFunctions(unittest.TestCase):
    def test_get_all_base_tools(self) -> None:
        reg = ToolRegistry([_make_tool("A"), _make_tool("B")])
        tools = get_all_base_tools(reg)
        self.assertEqual(len(tools), 2)

    def test_filter_tools_by_deny_rules(self) -> None:
        tools = [_make_tool("Read"), _make_tool("Write"), _make_tool("Bash")]
        pc = ToolPermissionContext.from_iterables(
            deny_names=["Bash"],
            deny_prefixes=[],
        )
        filtered = filter_tools_by_deny_rules(tools, pc)
        names = [t.name for t in filtered]
        self.assertIn("Read", names)
        self.assertIn("Write", names)
        self.assertNotIn("Bash", names)

    def test_get_tools_filters_disabled(self) -> None:
        t1 = _make_tool("Enabled", is_enabled=lambda: True)
        t2 = _make_tool("Disabled", is_enabled=lambda: False)
        reg = ToolRegistry([t1, t2])
        pc = ToolPermissionContext()
        tools = get_tools(reg, pc)
        names = [t.name for t in tools]
        self.assertIn("Enabled", names)
        self.assertNotIn("Disabled", names)

    def test_assemble_tool_pool_sorts_by_name(self) -> None:
        reg = ToolRegistry([_make_tool("Zebra"), _make_tool("Alpha")])
        pc = ToolPermissionContext()
        pool = assemble_tool_pool(reg, pc)
        names = [t.name for t in pool]
        self.assertEqual(names, ["Alpha", "Zebra"])

    def test_assemble_tool_pool_merges_mcp(self) -> None:
        reg = ToolRegistry([_make_tool("Builtin")])
        mcp = [_make_tool("McpTool")]
        pc = ToolPermissionContext()
        pool = assemble_tool_pool(reg, pc, mcp_tools=mcp)
        names = [t.name for t in pool]
        self.assertIn("Builtin", names)
        self.assertIn("McpTool", names)

    def test_assemble_tool_pool_builtin_wins_over_mcp(self) -> None:
        reg = ToolRegistry([_make_tool("Shared")])
        mcp = [_make_tool("Shared")]
        pc = ToolPermissionContext()
        pool = assemble_tool_pool(reg, pc, mcp_tools=mcp)
        self.assertEqual(len([t for t in pool if t.name == "Shared"]), 1)


class TestGetMergedTools(unittest.TestCase):
    def test_merged_without_mcp(self) -> None:
        reg = ToolRegistry([_make_tool("A"), _make_tool("B")])
        pc = ToolPermissionContext()
        tools = get_merged_tools(reg, pc)
        names = [t.name for t in tools]
        self.assertIn("A", names)
        self.assertIn("B", names)

    def test_merged_with_mcp(self) -> None:
        reg = ToolRegistry([_make_tool("Builtin")])
        mcp = [_make_tool("McpTool")]
        pc = ToolPermissionContext()
        tools = get_merged_tools(reg, pc, mcp_tools=mcp)
        names = [t.name for t in tools]
        self.assertIn("Builtin", names)
        self.assertIn("McpTool", names)

    def test_merged_no_dedup(self) -> None:
        reg = ToolRegistry([_make_tool("Shared")])
        mcp = [_make_tool("Shared")]
        pc = ToolPermissionContext()
        tools = get_merged_tools(reg, pc, mcp_tools=mcp)
        shared_count = len([t for t in tools if t.name == "Shared"])
        self.assertEqual(shared_count, 2)


class TestDefaultRegistry(unittest.TestCase):
    def test_default_registry_has_core_tools(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        for name in ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent", "ToolSearch"]:
            self.assertIsNotNone(reg.get(name), f"missing tool: {name}")

    def test_default_registry_tool_count(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        self.assertGreater(len(reg.list_tools()), 20)


if __name__ == "__main__":
    unittest.main()
