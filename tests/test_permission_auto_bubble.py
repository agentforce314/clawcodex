"""Tests for ``auto`` and ``bubble`` mode integration in has_permissions_to_use_tool.

Both modes are internal (not in ``EXTERNAL_PERMISSION_MODES``); they are
exercised by sub-agent and headless flows. Parity references:

- auto: ``typescript/src/utils/permissions/permissions.ts:520-927``
- bubble: ``typescript/src/utils/permissions/permissions.ts`` (escalation
  via interactiveHandler/coordinator) + book ch01:126, ch06:211-213.
"""
from __future__ import annotations

import unittest
from typing import Any

from src.permissions.check import has_permissions_to_use_tool
from src.permissions.types import (
    AsyncAgentDecisionReason,
    ClassifierDecisionReason,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    PermissionResult,
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
        self, tool_input: dict[str, Any], context: Any,
    ) -> PermissionResult:
        if self._perm_result is not None:
            return self._perm_result
        return PermissionPassthroughResult()


class TestAutoMode(unittest.TestCase):
    def test_auto_allows_read_only_tool(self) -> None:
        ctx = ToolPermissionContext(mode="auto")
        tool = _MockTool(name="Read")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "allow")
        reason = decision.decision_reason
        self.assertIsInstance(reason, ClassifierDecisionReason)
        assert isinstance(reason, ClassifierDecisionReason)
        self.assertEqual(reason.classifier, "auto-mode")

    def test_auto_denies_unknown_mcp_tool(self) -> None:
        ctx = ToolPermissionContext(mode="auto")
        tool = _MockTool(name="mcp__unknown__do_thing", is_mcp=True)
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")
        reason = decision.decision_reason
        self.assertIsInstance(reason, ClassifierDecisionReason)

    def test_auto_safety_check_non_classifier_approvable_returns_ask(self) -> None:
        # When tool.check_permissions returns an ask with safetyCheck +
        # classifier_approvable=False, auto mode must NOT auto-allow/deny —
        # it should surface the original ask. Parity with TS lines 530-548.
        safety_ask = PermissionAskDecision(
            behavior="ask",
            message="Sensitive path",
            decision_reason=SafetyCheckDecisionReason(
                reason="UNC path detected",
                classifier_approvable=False,
            ),
        )
        ctx = ToolPermissionContext(mode="auto")
        tool = _MockTool(name="Read", perm_result=safety_ask)
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")

    def test_auto_safety_check_non_classifier_approvable_in_headless_denies(self) -> None:
        safety_ask = PermissionAskDecision(
            behavior="ask",
            message="Sensitive path",
            decision_reason=SafetyCheckDecisionReason(
                reason="UNC path detected",
                classifier_approvable=False,
            ),
        )
        ctx = ToolPermissionContext(
            mode="auto", should_avoid_permission_prompts=True,
        )
        tool = _MockTool(name="Read", perm_result=safety_ask)
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")
        self.assertIsInstance(decision.decision_reason, AsyncAgentDecisionReason)

    def test_auto_classifier_approvable_safety_check_runs_classifier(self) -> None:
        # safetyCheck with classifier_approvable=True should fall through to
        # the classifier (parity with TS comment lines 530-548). Read of a
        # protected file is one such case — classifier deems it allowed.
        safety_ask = PermissionAskDecision(
            behavior="ask",
            message="Protected file",
            decision_reason=SafetyCheckDecisionReason(
                reason="Protected directory",
                classifier_approvable=True,
            ),
        )
        ctx = ToolPermissionContext(mode="auto")
        tool = _MockTool(name="Read", perm_result=safety_ask)
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        # auto_mode_classify("Read", ...) returns allow for the Read tool
        self.assertEqual(decision.behavior, "allow")

    def test_default_mode_does_not_invoke_classifier(self) -> None:
        # Sanity: classifier only fires under mode="auto"
        ctx = ToolPermissionContext(mode="default")
        tool = _MockTool(name="Read")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")


class TestBubbleMode(unittest.TestCase):
    def test_bubble_denies_with_async_agent_reason(self) -> None:
        ctx = ToolPermissionContext(mode="bubble")
        tool = _MockTool(name="TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")
        self.assertIsInstance(decision.decision_reason, AsyncAgentDecisionReason)
        assert isinstance(decision.decision_reason, AsyncAgentDecisionReason)
        self.assertIn("bubble", decision.decision_reason.reason.lower())

    def test_bubble_does_not_block_allow_decisions(self) -> None:
        # If the tool itself returns allow (via rule or its own check), bubble
        # mode should not interfere — it's only on ask decisions that need
        # human approval.
        ctx = ToolPermissionContext(
            mode="bubble", always_allow_rules={"session": ["TestTool"]},
        )
        tool = _MockTool(name="TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "allow")

    def test_bubble_does_not_block_deny_decisions(self) -> None:
        ctx = ToolPermissionContext(
            mode="bubble", always_deny_rules={"session": ["TestTool"]},
        )
        tool = _MockTool(name="TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")
        # The deny reason must come from the rule, not the bubble stub
        self.assertEqual(decision.decision_reason.type, "rule")


class TestModeOrderingInOuterFlow(unittest.TestCase):
    """The outer ask-transformation order in has_permissions_to_use_tool:
    1. dontAsk → deny (highest priority)
    2. auto → classifier
    3. bubble → escalation deny
    4. should_avoid_permission_prompts → headless deny
    """

    def test_dontAsk_takes_priority_over_other_modes(self) -> None:
        # dontAsk should win even with a non-classifier-approvable safety
        # check (irrelevant here because mode is dontAsk, not auto).
        ctx = ToolPermissionContext(mode="dontAsk")
        tool = _MockTool(name="TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")
        self.assertEqual(decision.decision_reason.type, "mode")

    def test_should_avoid_prompts_runs_after_auto(self) -> None:
        # In auto mode + should_avoid_prompts, the classifier still runs
        # first; when it allows, we don't fall through to the headless deny.
        ctx = ToolPermissionContext(
            mode="auto", should_avoid_permission_prompts=True,
        )
        tool = _MockTool(name="Read")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "allow")


if __name__ == "__main__":
    unittest.main()
