from __future__ import annotations

import unittest

from src.permissions.rule_parser import (
    escape_rule_content,
    normalize_legacy_tool_name,
    permission_rule_value_from_string,
    permission_rule_value_to_string,
    unescape_rule_content,
)
from src.permissions.rules import (
    get_allow_rules,
    get_ask_rule_for_tool,
    get_ask_rules,
    get_deny_rule_for_tool,
    get_deny_rules,
    get_rule_by_contents_for_tool,
    tool_always_allowed_rule,
)
from src.permissions.types import (
    PermissionRuleValue,
    ToolPermissionContext,
)


class _FakeTool:
    def __init__(self, name: str, is_mcp: bool = False) -> None:
        self._name = name
        self._is_mcp = is_mcp

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_mcp(self) -> bool:
        return self._is_mcp


class TestRuleParser(unittest.TestCase):
    def test_simple_tool_name(self) -> None:
        rv = permission_rule_value_from_string("Bash")
        self.assertEqual(rv.tool_name, "Bash")
        self.assertIsNone(rv.rule_content)

    def test_tool_name_with_content(self) -> None:
        rv = permission_rule_value_from_string("Bash(npm install)")
        self.assertEqual(rv.tool_name, "Bash")
        self.assertEqual(rv.rule_content, "npm install")

    def test_escaped_parentheses(self) -> None:
        rv = permission_rule_value_from_string("Bash(print\\(1\\))")
        self.assertEqual(rv.tool_name, "Bash")
        self.assertEqual(rv.rule_content, "print(1)")

    def test_empty_content_treated_as_tool_only(self) -> None:
        rv = permission_rule_value_from_string("Bash()")
        self.assertEqual(rv.tool_name, "Bash")
        self.assertIsNone(rv.rule_content)

    def test_wildcard_content_treated_as_tool_only(self) -> None:
        rv = permission_rule_value_from_string("Bash(*)")
        self.assertEqual(rv.tool_name, "Bash")
        self.assertIsNone(rv.rule_content)

    def test_no_tool_name_before_paren(self) -> None:
        rv = permission_rule_value_from_string("(content)")
        self.assertEqual(rv.tool_name, "(content)")
        self.assertIsNone(rv.rule_content)

    def test_unmatched_paren(self) -> None:
        rv = permission_rule_value_from_string("Bash(content")
        self.assertEqual(rv.tool_name, "Bash(content")
        self.assertIsNone(rv.rule_content)

    def test_legacy_tool_name_normalization(self) -> None:
        self.assertEqual(normalize_legacy_tool_name("Task"), "Agent")
        self.assertEqual(normalize_legacy_tool_name("KillShell"), "TaskStop")
        self.assertEqual(normalize_legacy_tool_name("Bash"), "Bash")

    def test_roundtrip(self) -> None:
        original = "Bash(npm install)"
        rv = permission_rule_value_from_string(original)
        back = permission_rule_value_to_string(rv)
        self.assertEqual(back, original)

    def test_roundtrip_with_parens(self) -> None:
        rv = PermissionRuleValue(tool_name="Bash", rule_content="print(1)")
        s = permission_rule_value_to_string(rv)
        rv2 = permission_rule_value_from_string(s)
        self.assertEqual(rv2.tool_name, "Bash")
        self.assertEqual(rv2.rule_content, "print(1)")


class TestEscapeUnescape(unittest.TestCase):
    def test_escape(self) -> None:
        self.assertEqual(escape_rule_content("print(1)"), "print\\(1\\)")

    def test_unescape(self) -> None:
        self.assertEqual(unescape_rule_content("print\\(1\\)"), "print(1)")

    def test_roundtrip(self) -> None:
        original = "hello(world)\\test"
        self.assertEqual(unescape_rule_content(escape_rule_content(original)), original)


class TestRuleMatching(unittest.TestCase):
    def test_deny_rule_matches_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["Bash"]},
        )
        tool = _FakeTool("Bash")
        rule = get_deny_rule_for_tool(ctx, tool)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.rule_value.tool_name, "Bash")

    def test_deny_rule_no_match(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["Write"]},
        )
        tool = _FakeTool("Read")
        rule = get_deny_rule_for_tool(ctx, tool)
        self.assertIsNone(rule)

    def test_ask_rule_matches_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_ask_rules={"session": ["Write"]},
        )
        tool = _FakeTool("Write")
        rule = get_ask_rule_for_tool(ctx, tool)
        self.assertIsNotNone(rule)

    def test_allow_rule_matches_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["Bash"]},
        )
        tool = _FakeTool("Bash")
        rule = tool_always_allowed_rule(ctx, tool)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.rule_behavior, "allow")

    def test_allow_rule_no_match(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["Bash"]},
        )
        tool = _FakeTool("Write")
        rule = tool_always_allowed_rule(ctx, tool)
        self.assertIsNone(rule)

    def test_content_rule_does_not_match_tool_level(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["Bash(npm install)"]},
        )
        tool = _FakeTool("Bash")
        rule = get_deny_rule_for_tool(ctx, tool)
        self.assertIsNone(rule)

    def test_get_rule_by_contents_for_tool(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["Bash(npm install)", "Bash(npm test)"]},
        )
        rules = get_rule_by_contents_for_tool(ctx, "Bash", "allow")
        self.assertIn("npm install", rules)
        self.assertIn("npm test", rules)
        self.assertEqual(len(rules), 2)


class TestRuleCollection(unittest.TestCase):
    def test_get_allow_rules(self) -> None:
        ctx = ToolPermissionContext(
            always_allow_rules={"session": ["Bash", "Read"], "userSettings": ["Write"]},
        )
        rules = get_allow_rules(ctx)
        names = [r.rule_value.tool_name for r in rules]
        self.assertIn("Bash", names)
        self.assertIn("Read", names)
        self.assertIn("Write", names)
        self.assertEqual(len(rules), 3)

    def test_get_deny_rules(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["Bash"]},
        )
        rules = get_deny_rules(ctx)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_value.tool_name, "Bash")
        self.assertEqual(rules[0].rule_behavior, "deny")

    def test_get_ask_rules(self) -> None:
        ctx = ToolPermissionContext(
            always_ask_rules={"projectSettings": ["Write"]},
        )
        rules = get_ask_rules(ctx)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].source, "projectSettings")

    def test_mcp_wildcard_rule_match(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["mcp__myserver"]},
        )
        tool = _FakeTool("mcp__myserver__doStuff", is_mcp=True)
        rule = get_deny_rule_for_tool(ctx, tool)
        self.assertIsNotNone(rule)

    def test_mcp_wildcard_no_match_different_server(self) -> None:
        ctx = ToolPermissionContext(
            always_deny_rules={"session": ["mcp__serverA"]},
        )
        tool = _FakeTool("mcp__serverB__doStuff", is_mcp=True)
        rule = get_deny_rule_for_tool(ctx, tool)
        self.assertIsNone(rule)


if __name__ == "__main__":
    unittest.main()
