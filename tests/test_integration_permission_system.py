from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.permissions import (
    DANGEROUS_BASH_PATTERNS,
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDecision,
    PermissionDenyDecision,
    PermissionMode,
    PermissionPassthroughResult,
    PermissionResult,
    PermissionRule,
    PermissionRuleValue,
    ToolPermissionContext,
    check_path_safety_for_auto_edit,
    has_permissions_to_use_tool,
    is_dangerous_bash_permission,
    permission_mode_from_string,
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)
from src.permissions.handler import handle_permission_ask
from src.permissions.loader import apply_rules_to_context, settings_to_rules
from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import ToolRegistry


class TestImportAllModules(unittest.TestCase):
    def test_import_permissions_package(self) -> None:
        import src.permissions
        self.assertTrue(hasattr(src.permissions, "has_permissions_to_use_tool"))

    def test_import_types(self) -> None:
        from src.permissions.types import (
            PermissionAllowDecision,
            PermissionAskDecision,
            PermissionDenyDecision,
            PermissionPassthroughResult,
            ToolPermissionContext,
        )
        ctx = ToolPermissionContext()
        self.assertEqual(ctx.mode, "default")

    def test_import_modes(self) -> None:
        from src.permissions.modes import permission_mode_title
        self.assertIsInstance(permission_mode_title("default"), str)

    def test_import_rule_parser(self) -> None:
        from src.permissions.rule_parser import permission_rule_value_from_string
        rv = permission_rule_value_from_string("Bash")
        self.assertEqual(rv.tool_name, "Bash")

    def test_import_rules(self) -> None:
        from src.permissions.rules import get_allow_rules
        self.assertIsInstance(get_allow_rules(ToolPermissionContext()), list)

    def test_import_check(self) -> None:
        from src.permissions.check import has_permissions_to_use_tool
        self.assertTrue(callable(has_permissions_to_use_tool))

    def test_import_filesystem(self) -> None:
        from src.permissions.filesystem import DANGEROUS_FILES
        self.assertGreater(len(DANGEROUS_FILES), 0)

    def test_import_bash_security(self) -> None:
        from src.permissions.bash_security import DANGEROUS_BASH_PATTERNS
        self.assertGreater(len(DANGEROUS_BASH_PATTERNS), 0)

    def test_import_handler(self) -> None:
        from src.permissions.handler import handle_permission_ask
        self.assertTrue(callable(handle_permission_ask))

    def test_import_loader(self) -> None:
        from src.permissions.loader import settings_to_rules
        self.assertTrue(callable(settings_to_rules))


def _noop_call(tool_input: dict[str, Any], context: Any) -> ToolResult:
    return ToolResult(name="TestTool", output={"ok": True})


class _MockToolForPerm:
    def __init__(
        self,
        name: str = "TestTool",
        is_mcp: bool = False,
        perm_result: PermissionResult | None = None,
    ) -> None:
        self._name = name
        self._is_mcp = is_mcp
        self._perm_result = perm_result

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_mcp(self) -> bool:
        return self._is_mcp

    def check_permissions(
        self, tool_input: dict[str, Any], context: Any,
    ) -> PermissionResult:
        if self._perm_result is not None:
            return self._perm_result
        return PermissionPassthroughResult()


class TestEndToEndPermissionFlow(unittest.TestCase):
    def test_deny_rule_denies_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["TestTool"]},
        )
        tool = _MockToolForPerm()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")
        self.assertIsInstance(result, PermissionDenyDecision)

    def test_allow_rule_allows_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["TestTool"]},
        )
        tool = _MockToolForPerm()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "allow")
        self.assertIsInstance(result, PermissionAllowDecision)

    def test_no_rules_results_in_ask(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockToolForPerm()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")
        self.assertIsInstance(result, PermissionAskDecision)

    def test_bypass_mode_allows(self) -> None:
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _MockToolForPerm()
        result = has_permissions_to_use_tool(tool, {"key": "val"}, ctx)
        self.assertEqual(result.behavior, "allow")
        self.assertEqual(result.updated_input, {"key": "val"})

    def test_dont_ask_mode_converts_ask_to_deny(self) -> None:
        ctx = ToolPermissionContext(mode="dontAsk")
        tool = _MockToolForPerm()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")


class TestFilesystemPermissionIntegration(unittest.TestCase):
    def test_protected_path_returns_safety_check(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.git/config")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_normal_path_returns_none(self) -> None:
        result = check_path_safety_for_auto_edit("/project/src/main.py")
        self.assertIsNone(result)


class TestRegistryDispatchWithNewPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dispatch_with_deny_rule(self) -> None:
        def _check(inp: dict[str, Any], ctx: Any) -> Any:
            return PermissionDenyDecision(message="blocked")

        tool = build_tool(
            name="Blocked",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            check_permissions=_check,
        )
        reg = ToolRegistry([tool])
        result = reg.dispatch(ToolCall(name="Blocked", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertIn("blocked", result.output["error"])

    def test_dispatch_with_allow_via_handler(self) -> None:
        def _check(inp: dict[str, Any], ctx: Any) -> Any:
            return PermissionAskDecision(message="confirm?")

        tool = build_tool(
            name="NeedApproval",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
            check_permissions=_check,
        )
        reg = ToolRegistry([tool])
        self.ctx.permission_handler = lambda name, msg, sug: (True, False)
        result = reg.dispatch(ToolCall(name="NeedApproval", input={}), self.ctx)
        self.assertFalse(result.is_error)

    def test_dispatch_regular_tool_no_rules(self) -> None:
        tool = build_tool(
            name="Simple",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        reg = ToolRegistry([tool])
        self.ctx.permission_handler = lambda name, msg, sug: (True, False)
        result = reg.dispatch(ToolCall(name="Simple", input={}), self.ctx)
        self.assertFalse(result.is_error)


class TestSettingsLoader(unittest.TestCase):
    def test_settings_to_rules_parses_correctly(self) -> None:
        data = {
            "allow": ["Bash(npm install)", "Read"],
            "deny": ["Write"],
        }
        rules = settings_to_rules(data, "userSettings")
        self.assertEqual(len(rules), 3)
        allow_rules = [r for r in rules if r.rule_behavior == "allow"]
        deny_rules = [r for r in rules if r.rule_behavior == "deny"]
        self.assertEqual(len(allow_rules), 2)
        self.assertEqual(len(deny_rules), 1)

    def test_apply_rules_to_context(self) -> None:
        ctx = ToolPermissionContext()
        rules = [
            PermissionRule(
                source="session",
                rule_behavior="allow",
                rule_value=PermissionRuleValue(tool_name="Bash"),
            ),
            PermissionRule(
                source="session",
                rule_behavior="deny",
                rule_value=PermissionRuleValue(tool_name="Write"),
            ),
        ]
        new_ctx = apply_rules_to_context(ctx, rules)
        self.assertIn("session", new_ctx.always_allow_rules)
        self.assertIn("session", new_ctx.always_deny_rules)
        self.assertIn("Bash", new_ctx.always_allow_rules["session"])
        self.assertIn("Write", new_ctx.always_deny_rules["session"])


class TestHandlePermissionAsk(unittest.TestCase):
    def test_no_handler_returns_deny(self) -> None:
        decision = PermissionAskDecision(message="confirm?")
        result = handle_permission_ask("TestTool", decision)
        self.assertEqual(result.behavior, "deny")

    def test_handler_allows(self) -> None:
        decision = PermissionAskDecision(message="confirm?")

        def handler(name: str, msg: str, suggestions: Any) -> tuple[bool, Any]:
            return True, None

        result = handle_permission_ask("TestTool", decision, handler)
        self.assertEqual(result.behavior, "allow")

    def test_handler_denies(self) -> None:
        decision = PermissionAskDecision(message="confirm?")

        def handler(name: str, msg: str, suggestions: Any) -> tuple[bool, Any]:
            return False, None

        result = handle_permission_ask("TestTool", decision, handler)
        self.assertEqual(result.behavior, "deny")


class TestBuildAppSmoke(unittest.TestCase):
    def test_build_default_registry_succeeds(self) -> None:
        reg = build_default_registry()
        tools = reg.list_tools()
        self.assertGreater(len(tools), 0)

    def test_pip_install_editable(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, f"pip install failed: {result.stderr}")

    def test_main_cli_tools_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "tools", "--limit", "5"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"CLI tools command failed: {result.stderr}")
        self.assertIn("Tool entries:", result.stdout)


class TestToolPermissionContextHelpers(unittest.TestCase):
    def test_tool_permission_context_from_iterables(self) -> None:
        ctx = ToolPermissionContext.from_iterables(["Write"], ["mcp"])
        self.assertTrue(ctx.blocks("Write"))
        self.assertFalse(ctx.blocks("Read"))

    def test_new_permission_decision_types(self) -> None:
        allow = PermissionAllowDecision()
        self.assertEqual(allow.behavior, "allow")
        deny = PermissionDenyDecision(message="no")
        self.assertEqual(deny.behavior, "deny")
        ask = PermissionAskDecision(message="sure?")
        self.assertEqual(ask.behavior, "ask")
        pt = PermissionPassthroughResult(message="maybe")
        self.assertEqual(pt.behavior, "passthrough")


if __name__ == "__main__":
    unittest.main()
