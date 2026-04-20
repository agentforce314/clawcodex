from __future__ import annotations

import unittest
from typing import Any

from src.permissions.check import (
    check_rule_based_permissions,
    has_permissions_to_use_tool,
    has_permissions_to_use_tool_inner,
)
from src.permissions.types import (
    ModeDecisionReason,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    PermissionResult,
    RuleDecisionReason,
    SafetyCheckDecisionReason,
    ToolPermissionContext,
)


class _MockTool:
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
        self, tool_input: dict[str, Any], context: Any
    ) -> PermissionResult:
        if self._perm_result is not None:
            return self._perm_result
        return PermissionPassthroughResult()


class TestStep1a_DenyRule(unittest.TestCase):
    def test_denied_tool_returns_deny(self) -> None:
        ctx = ToolPermissionContext(always_deny_rules={"session": ["TestTool"]})
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")
        self.assertIsInstance(result, PermissionDenyDecision)


class TestStep1b_AskRule(unittest.TestCase):
    def test_ask_rule_returns_ask(self) -> None:
        ctx = ToolPermissionContext(always_ask_rules={"session": ["TestTool"]})
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")
        self.assertIsInstance(result, PermissionAskDecision)


class TestStep1c_ToolCheckPermissions(unittest.TestCase):
    def test_tool_deny_propagated(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockTool(perm_result=PermissionDenyDecision(message="forbidden"))
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")
        self.assertIn("forbidden", result.message)

    def test_tool_allow_propagated(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockTool(perm_result=PermissionAllowDecision())
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "allow")


class TestStep1g_SafetyCheck(unittest.TestCase):
    def test_safety_check_bypass_immune(self) -> None:
        safety_ask = PermissionAskDecision(
            message="safety!",
            decision_reason=SafetyCheckDecisionReason(
                reason="protected file",
                classifier_approvable=True,
            ),
        )
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _MockTool(perm_result=safety_ask)
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")


class TestStep2a_BypassMode(unittest.TestCase):
    def test_bypass_mode_allows(self) -> None:
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "allow")
        self.assertIsInstance(result, PermissionAllowDecision)
        self.assertIsInstance(result.decision_reason, ModeDecisionReason)

    def test_plan_mode_with_bypass_available_allows(self) -> None:
        ctx = ToolPermissionContext(
            mode="plan",
            is_bypass_permissions_mode_available=True,
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "allow")

    def test_plan_mode_without_bypass_does_not_auto_allow(self) -> None:
        ctx = ToolPermissionContext(
            mode="plan",
            is_bypass_permissions_mode_available=False,
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertNotEqual(result.behavior, "allow")


class TestStep2b_AllowRule(unittest.TestCase):
    def test_allow_rule_allows(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["TestTool"]},
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "allow")
        self.assertIsInstance(result, PermissionAllowDecision)
        self.assertIsInstance(result.decision_reason, RuleDecisionReason)


class TestStep3_PassthroughToAsk(unittest.TestCase):
    def test_passthrough_converted_to_ask(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")
        self.assertIsInstance(result, PermissionAskDecision)


class TestDontAskMode(unittest.TestCase):
    def test_ask_becomes_deny(self) -> None:
        ctx = ToolPermissionContext(mode="dontAsk")
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")
        self.assertIsInstance(result, PermissionDenyDecision)


class TestAvoidPermissionPrompts(unittest.TestCase):
    def test_ask_becomes_deny_when_prompts_unavailable(self) -> None:
        ctx = ToolPermissionContext(should_avoid_permission_prompts=True)
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")


class TestRulePrecedence(unittest.TestCase):
    def test_deny_overrides_allow(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["TestTool"]},
            always_allow_rules={"session": ["TestTool"]},
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")

    def test_ask_checked_before_allow(self) -> None:
        ctx = ToolPermissionContext(
            always_ask_rules={"session": ["TestTool"]},
            always_allow_rules={"session": ["TestTool"]},
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")


class TestCheckRuleBasedPermissions(unittest.TestCase):
    def test_returns_none_when_no_rules_match(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockTool()
        result = check_rule_based_permissions(tool, {}, ctx)
        self.assertIsNone(result)

    def test_returns_deny_for_deny_rule(self) -> None:
        ctx = ToolPermissionContext(always_deny_rules={"session": ["TestTool"]})
        tool = _MockTool()
        result = check_rule_based_permissions(tool, {}, ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "deny")

    def test_returns_ask_for_ask_rule(self) -> None:
        ctx = ToolPermissionContext(always_ask_rules={"session": ["TestTool"]})
        tool = _MockTool()
        result = check_rule_based_permissions(tool, {}, ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")


class TestEndToEnd(unittest.TestCase):
    def test_no_rules_passthrough_becomes_ask(self) -> None:
        ctx = ToolPermissionContext()
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "ask")

    def test_allowed_tool_with_bypass_mode(self) -> None:
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {"key": "val"}, ctx)
        self.assertEqual(result.behavior, "allow")
        self.assertEqual(result.updated_input, {"key": "val"})

    def test_deny_rule_always_wins(self) -> None:
        ctx = ToolPermissionContext(
            mode="bypassPermissions",
            always_deny_rules={"session": ["TestTool"]},
        )
        tool = _MockTool()
        result = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(result.behavior, "deny")


if __name__ == "__main__":
    unittest.main()
