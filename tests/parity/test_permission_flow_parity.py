"""WS-10: Structural parity — permission check flow produces same decisions as TS.

Verifies:
- Permission test vectors from ts_permission_vectors.json produce expected decisions
- 10-step permission check flow ordering matches TS
- Permission modes, rule sources, and decision types match
- Deny > Ask > Allow priority ordering
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.permissions.check import has_permissions_to_use_tool
from src.permissions.types import (
    PERMISSION_MODES,
    PERMISSION_RULE_SOURCES,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    ToolPermissionContext,
)

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


def _make_mock_tool(name: str, is_mcp: bool = False):
    """Create a minimal mock tool for permission checking."""
    tool = MagicMock()
    tool.name = name
    tool.is_mcp = is_mcp
    tool.check_permissions = MagicMock(
        return_value=PermissionPassthroughResult(
            behavior="passthrough",
            message=f"Claude wants to use {name}. Allow?",
        )
    )
    # Default: not user-interactive
    if hasattr(tool, "requires_user_interaction"):
        del tool.requires_user_interaction
    return tool


class TestPermissionModesParity(unittest.TestCase):
    """Permission modes match TS types/permissions.ts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_permission_vectors.json")

    def test_all_modes_present(self) -> None:
        ts_modes = set(self.snapshot["permission_modes"])
        py_modes = set(PERMISSION_MODES)
        self.assertEqual(ts_modes, py_modes)

    def test_all_rule_sources_present(self) -> None:
        ts_sources = set(self.snapshot["rule_sources"])
        py_sources = set(PERMISSION_RULE_SOURCES)
        self.assertEqual(ts_sources, py_sources)

    def test_all_decision_types_present(self) -> None:
        ts_decisions = set(self.snapshot["decision_types"])
        expected = {"allow", "deny", "ask"}
        self.assertEqual(ts_decisions, expected)


class TestPermissionVectorParity(unittest.TestCase):
    """Permission test vectors produce same decisions as TS."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_permission_vectors.json")

    def _run_vector(self, vector: dict) -> str:
        ctx = ToolPermissionContext(
            mode=vector.get("mode", "default"),
            always_deny_rules=vector.get("deny_rules", {}),
            always_allow_rules=vector.get("allow_rules", {}),
            always_ask_rules=vector.get("ask_rules", {}),
            is_bypass_permissions_mode_available=vector.get("is_bypass_available", False),
            should_avoid_permission_prompts=vector.get("should_avoid_prompts", False),
        )
        tool = _make_mock_tool(vector["tool_name"], vector.get("tool_is_mcp", False))
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        return decision.behavior

    def test_deny_rule_blocks_tool(self) -> None:
        vector = self._find_vector("deny_rule_blocks_tool")
        self.assertEqual(self._run_vector(vector), "deny")

    def test_ask_rule_prompts(self) -> None:
        vector = self._find_vector("ask_rule_prompts")
        self.assertEqual(self._run_vector(vector), "ask")

    def test_allow_rule_permits(self) -> None:
        vector = self._find_vector("allow_rule_permits")
        self.assertEqual(self._run_vector(vector), "allow")

    def test_bypass_mode_allows(self) -> None:
        vector = self._find_vector("bypass_mode_allows")
        self.assertEqual(self._run_vector(vector), "allow")

    def test_deny_overrides_bypass(self) -> None:
        vector = self._find_vector("deny_overrides_bypass")
        self.assertEqual(self._run_vector(vector), "deny")

    def test_dontAsk_mode_denies_ask(self) -> None:
        vector = self._find_vector("dontAsk_mode_denies_ask")
        self.assertEqual(self._run_vector(vector), "deny")

    def test_default_mode_passthrough_asks(self) -> None:
        vector = self._find_vector("default_mode_passthrough_asks")
        self.assertEqual(self._run_vector(vector), "ask")

    def test_deny_rule_priority_over_allow(self) -> None:
        vector = self._find_vector("deny_rule_priority_over_allow")
        self.assertEqual(self._run_vector(vector), "deny")

    def test_ask_rule_checked_before_tool_custom(self) -> None:
        vector = self._find_vector("ask_rule_checked_before_tool_custom")
        self.assertEqual(self._run_vector(vector), "ask")

    def test_plan_mode_with_bypass_available(self) -> None:
        vector = self._find_vector("plan_mode_with_bypass_available")
        self.assertEqual(self._run_vector(vector), "allow")

    def test_should_avoid_prompts_denies_ask(self) -> None:
        vector = self._find_vector("should_avoid_prompts_denies_ask")
        self.assertEqual(self._run_vector(vector), "deny")

    def _find_vector(self, vector_id: str) -> dict:
        for v in self.snapshot["test_vectors"]:
            if v["id"] == vector_id:
                return v
        self.fail(f"Vector '{vector_id}' not found in snapshot")


class TestPermissionCheckFlowOrdering(unittest.TestCase):
    """The 10-step permission check flow follows TS ordering."""

    def test_deny_checked_before_ask(self) -> None:
        """Step 1a (deny) comes before step 1b (ask)."""
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["TestTool"]},
            always_ask_rules={"session": ["TestTool"]},
        )
        tool = _make_mock_tool("TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")

    def test_ask_rule_checked_before_tool_custom_check(self) -> None:
        """Step 1b (ask rule) comes before step 1c (tool custom check)."""
        ctx = ToolPermissionContext(
            always_ask_rules={"session": ["TestTool"]},
        )
        tool = _make_mock_tool("TestTool")
        # Tool would allow, but ask rule takes priority
        tool.check_permissions = MagicMock(
            return_value=PermissionAllowDecision(behavior="allow")
        )
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")
        # check_permissions should NOT have been called
        tool.check_permissions.assert_not_called()

    def test_tool_deny_overrides_bypass_mode(self) -> None:
        """Tool custom deny (step 1c/1d) is respected even in bypass mode."""
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _make_mock_tool("TestTool")
        tool.check_permissions = MagicMock(
            return_value=PermissionDenyDecision(
                behavior="deny",
                message="Denied by tool",
            )
        )
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "deny")

    def test_bypass_mode_allows_passthrough(self) -> None:
        """Step 2a: bypass mode converts passthrough to allow."""
        ctx = ToolPermissionContext(mode="bypassPermissions")
        tool = _make_mock_tool("TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "allow")

    def test_allow_rule_checked_after_bypass(self) -> None:
        """Step 2b: allow rules checked after bypass mode."""
        ctx = ToolPermissionContext(
            mode="default",
            always_allow_rules={"session": ["TestTool"]},
        )
        tool = _make_mock_tool("TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "allow")

    def test_default_passthrough_becomes_ask(self) -> None:
        """Step 3: default passthrough → ask."""
        ctx = ToolPermissionContext(mode="default")
        tool = _make_mock_tool("TestTool")
        decision = has_permissions_to_use_tool(tool, {}, ctx)
        self.assertEqual(decision.behavior, "ask")


class TestPermissionDecisionTypes(unittest.TestCase):
    """Decision types have correct structure."""

    def test_allow_decision_has_updated_input(self) -> None:
        d = PermissionAllowDecision(behavior="allow", updated_input={"key": "val"})
        self.assertEqual(d.behavior, "allow")
        self.assertEqual(d.updated_input, {"key": "val"})

    def test_deny_decision_has_message(self) -> None:
        d = PermissionDenyDecision(behavior="deny", message="denied")
        self.assertEqual(d.behavior, "deny")
        self.assertEqual(d.message, "denied")

    def test_ask_decision_has_message_and_suggestions(self) -> None:
        d = PermissionAskDecision(behavior="ask", message="allow?")
        self.assertEqual(d.behavior, "ask")
        self.assertEqual(d.message, "allow?")


if __name__ == "__main__":
    unittest.main()
