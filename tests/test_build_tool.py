from __future__ import annotations

import unittest
from typing import Any

from src.tool_system.build_tool import (
    McpInfo,
    SearchOrReadResult,
    Tool,
    Tools,
    ValidationResult,
    build_tool,
    find_tool_by_name,
    tool_matches_name,
    TOOL_DEFAULTS,
)
from src.tool_system.context import ToolContext
from src.permissions.types import (
    PermissionDenyDecision,
    PermissionPassthroughResult,
    PermissionResult,
)
from src.tool_system.protocol import ToolResult


def _noop_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(name="Noop", output={"input": tool_input})


class TestBuildToolDefaults(unittest.TestCase):
    def test_build_tool_returns_tool_instance(self) -> None:
        t = build_tool(
            name="TestTool",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        self.assertIsInstance(t, Tool)
        self.assertEqual(t.name, "TestTool")

    def test_defaults_applied(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        self.assertTrue(t.is_enabled())
        self.assertFalse(t.is_concurrency_safe({}))
        self.assertFalse(t.is_read_only({}))
        self.assertFalse(t.is_destructive({}))
        self.assertEqual(t.check_permissions({}, None).behavior, "passthrough")
        self.assertEqual(t.user_facing_name(None), "T")
        self.assertEqual(t.to_auto_classifier_input({}), "")
        self.assertEqual(t.max_result_size_chars, 20_000)
        self.assertFalse(t.strict)
        self.assertFalse(t.should_defer)
        self.assertFalse(t.always_load)
        self.assertFalse(t.is_mcp)
        self.assertFalse(t.is_lsp)
        self.assertIsNone(t.validate_input)
        self.assertIsNone(t.get_path)
        self.assertEqual(t.aliases, ())
        self.assertIsNone(t.search_hint)
        self.assertIsNone(t.mcp_info)
        self.assertIsNone(t.input_json_schema)
        self.assertIsNone(t.interrupt_behavior)
        self.assertIsNone(t.is_search_or_read_command)
        self.assertIsNone(t.is_open_world)
        self.assertIsNone(t.requires_user_interaction)
        self.assertIsNone(t.inputs_equivalent)
        self.assertIsNone(t.backfill_observable_input)
        self.assertIsNone(t.prepare_permission_matcher)
        self.assertIsNone(t.get_tool_use_summary)
        self.assertIsNone(t.get_activity_description)

    def test_prompt_from_string(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            prompt="Do something.",
        )
        self.assertEqual(t.prompt(), "Do something.")

    def test_prompt_from_callable(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            prompt=lambda: "Dynamic prompt",
        )
        self.assertEqual(t.prompt(), "Dynamic prompt")

    def test_prompt_defaults_to_empty(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        self.assertEqual(t.prompt(), "")

    def test_description_from_string(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            description="A description",
        )
        self.assertEqual(t.description({}), "A description")

    def test_description_defaults_to_name(self) -> None:
        t = build_tool(
            name="MyTool",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        self.assertEqual(t.description({}), "MyTool")

    def test_overrides_applied(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            max_result_size_chars=50_000,
            strict=True,
            should_defer=True,
            always_load=True,
            is_mcp=True,
            aliases=("Alias1", "Alias2"),
            search_hint="find search",
            is_enabled=lambda: False,
            is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
            is_destructive=lambda _: True,
        )
        self.assertEqual(t.max_result_size_chars, 50_000)
        self.assertTrue(t.strict)
        self.assertTrue(t.should_defer)
        self.assertTrue(t.always_load)
        self.assertTrue(t.is_mcp)
        self.assertEqual(t.aliases, ("Alias1", "Alias2"))
        self.assertEqual(t.search_hint, "find search")
        self.assertFalse(t.is_enabled())
        self.assertTrue(t.is_concurrency_safe({}))
        self.assertTrue(t.is_read_only({}))
        self.assertTrue(t.is_destructive({}))


class TestToolMatchesName(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = build_tool(
            name="MyTool",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            aliases=("Mt", "my"),
        )

    def test_matches_exact_name(self) -> None:
        self.assertTrue(self.tool.matches_name("MyTool"))
        self.assertTrue(tool_matches_name(self.tool, "MyTool"))

    def test_matches_alias(self) -> None:
        self.assertTrue(self.tool.matches_name("Mt"))
        self.assertTrue(self.tool.matches_name("my"))

    def test_no_match(self) -> None:
        self.assertFalse(self.tool.matches_name("Other"))


class TestFindToolByName(unittest.TestCase):
    def test_find_existing(self) -> None:
        t1 = build_tool(name="A", input_schema={"type": "object"}, call=_noop_call)
        t2 = build_tool(name="B", input_schema={"type": "object"}, call=_noop_call, aliases=("b",))
        tools: Tools = [t1, t2]
        self.assertIs(find_tool_by_name(tools, "A"), t1)
        self.assertIs(find_tool_by_name(tools, "B"), t2)
        self.assertIs(find_tool_by_name(tools, "b"), t2)

    def test_find_missing(self) -> None:
        tools: Tools = []
        self.assertIsNone(find_tool_by_name(tools, "X"))


class TestValidationResult(unittest.TestCase):
    def test_ok(self) -> None:
        vr = ValidationResult.ok()
        self.assertTrue(vr.result)
        self.assertEqual(vr.message, "")

    def test_fail(self) -> None:
        vr = ValidationResult.fail("bad input", error_code=42)
        self.assertFalse(vr.result)
        self.assertEqual(vr.message, "bad input")
        self.assertEqual(vr.error_code, 42)


class TestToolCheckPermissions(unittest.TestCase):
    def test_custom_check_permissions(self) -> None:
        def _check(inp: dict[str, Any], ctx: ToolContext) -> PermissionResult:
            if inp.get("dangerous"):
                return PermissionDenyDecision(message="too dangerous")
            return PermissionPassthroughResult()

        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            check_permissions=_check,
        )
        self.assertEqual(t.check_permissions({}, None).behavior, "passthrough")
        self.assertEqual(t.check_permissions({"dangerous": True}, None).behavior, "deny")

    def test_validate_input(self) -> None:
        def _validate(inp: dict[str, Any], ctx: ToolContext) -> ValidationResult:
            if "x" not in inp:
                return ValidationResult.fail("x is required")
            return ValidationResult.ok()

        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            validate_input=_validate,
        )
        self.assertTrue(t.validate_input({"x": 1}, None).result)
        self.assertFalse(t.validate_input({}, None).result)


class TestNewToolFields(unittest.TestCase):
    def test_to_auto_classifier_input_override(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            to_auto_classifier_input=lambda inp: inp.get("cmd", ""),
        )
        self.assertEqual(t.to_auto_classifier_input({"cmd": "ls"}), "ls")

    def test_is_search_or_read_command(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            is_search_or_read_command=lambda _: SearchOrReadResult(is_search=True),
        )
        result = t.is_search_or_read_command({})
        self.assertTrue(result.is_search)
        self.assertFalse(result.is_read)
        self.assertFalse(result.is_list)

    def test_get_activity_description(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            get_activity_description=lambda inp: f"Doing {inp.get('x', '')}" if inp else None,
        )
        self.assertEqual(t.get_activity_description({"x": "stuff"}), "Doing stuff")
        self.assertIsNone(t.get_activity_description(None))

    def test_get_tool_use_summary(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            get_tool_use_summary=lambda inp: "summary" if inp is not None else None,
        )
        self.assertEqual(t.get_tool_use_summary({}), "summary")
        self.assertIsNone(t.get_tool_use_summary(None))

    def test_mcp_info(self) -> None:
        info = McpInfo(server_name="srv", tool_name="mytool")
        t = build_tool(
            name="mcp__srv__mytool",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            is_mcp=True,
            mcp_info=info,
        )
        self.assertTrue(t.is_mcp)
        self.assertEqual(t.mcp_info.server_name, "srv")
        self.assertEqual(t.mcp_info.tool_name, "mytool")

    def test_is_lsp(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            is_lsp=True,
        )
        self.assertTrue(t.is_lsp)

    def test_interrupt_behavior(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            interrupt_behavior=lambda: "cancel",
        )
        self.assertEqual(t.interrupt_behavior(), "cancel")

    def test_inputs_equivalent(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            inputs_equivalent=lambda a, b: a.get("x") == b.get("x"),
        )
        self.assertTrue(t.inputs_equivalent({"x": 1}, {"x": 1}))
        self.assertFalse(t.inputs_equivalent({"x": 1}, {"x": 2}))

    def test_requires_user_interaction(self) -> None:
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            requires_user_interaction=lambda: True,
        )
        self.assertTrue(t.requires_user_interaction())

    def test_input_json_schema(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        t = build_tool(
            name="T",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            input_json_schema=schema,
        )
        self.assertEqual(t.input_json_schema, schema)


class TestToolCall(unittest.TestCase):
    def test_call_invoked(self) -> None:
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        ctx = ToolContext(workspace_root=Path(tmp.name))
        t = build_tool(
            name="Echo",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            call=_noop_call,
        )
        result = t.call({"msg": "hi"}, ctx)
        self.assertEqual(result.output["input"]["msg"], "hi")
        tmp.cleanup()


class TestToolResultMcpMeta(unittest.TestCase):
    def test_mcp_meta_default_none(self) -> None:
        r = ToolResult(name="T", output={})
        self.assertIsNone(r.mcp_meta)

    def test_mcp_meta_set(self) -> None:
        meta = {"_meta": {"key": "val"}, "structuredContent": {"data": 1}}
        r = ToolResult(name="T", output={}, mcp_meta=meta)
        self.assertEqual(r.mcp_meta["_meta"]["key"], "val")
        self.assertEqual(r.mcp_meta["structuredContent"]["data"], 1)


if __name__ == "__main__":
    unittest.main()
