"""Phase D — Permission Integration Tests.

Full permission flow: setup → classify → check → decide.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from src.permissions.bash_parser.commands import CommandSafety
from src.permissions.bash_parser.parser import parse_command
from src.permissions.bash_security import (
    analyze_bash_command,
    check_bash_command_safety,
    is_dangerous_bash_permission,
)
from src.permissions.filesystem import (
    check_path_safety_for_auto_edit,
    check_read_permission_for_path,
)
from src.permissions.rule_parser import (
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)


class TestPermissionPipelineBash(unittest.TestCase):
    """Full bash permission pipeline: parse → classify → check."""

    def test_safe_command_pipeline(self) -> None:
        cmd = "echo hello"
        parsed = parse_command(cmd)
        self.assertIsNotNone(parsed)

        analysis = analyze_bash_command(cmd)
        self.assertEqual(analysis.safety, "safe")

        check = check_bash_command_safety(cmd)
        self.assertIsNone(check)  # No permission needed

    def test_read_only_pipeline(self) -> None:
        cmd = "cat /etc/hosts"
        analysis = analyze_bash_command(cmd)
        self.assertIn(analysis.safety, ("safe", "read_only"))

    def test_write_command_pipeline(self) -> None:
        cmd = "cp important.txt backup.txt"
        analysis = analyze_bash_command(cmd)
        self.assertIn(analysis.safety, ("write", "read_only", "safe"))

    def test_dangerous_command_pipeline(self) -> None:
        cmd = "sudo rm -rf /"
        analysis = analyze_bash_command(cmd)
        self.assertIn(analysis.safety, ("dangerous", "destructive", "unknown"))

        check = check_bash_command_safety(cmd)
        self.assertIsNotNone(check)
        self.assertEqual(check.behavior, "ask")

    def test_pipe_command_analysis(self) -> None:
        cmd = "cat file.txt | grep pattern | wc -l"
        parsed = parse_command(cmd)
        self.assertIsNotNone(parsed)
        analysis = analyze_bash_command(cmd)
        self.assertIsNotNone(analysis)

    def test_semicolon_chain(self) -> None:
        cmd = "cd /tmp; ls -la; pwd"
        parsed = parse_command(cmd)
        self.assertIsNotNone(parsed)


class TestPermissionPipelineFilesystem(unittest.TestCase):
    """Full filesystem permission pipeline."""

    def test_protected_file_blocked(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/.bashrc")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_env_file_blocked(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.env")
        self.assertIsNotNone(result)

    def test_git_dir_blocked(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.git/config")
        self.assertIsNotNone(result)

    def test_normal_source_allowed(self) -> None:
        result = check_path_safety_for_auto_edit("/project/src/main.py")
        self.assertIsNone(result)

    def test_lockfile_blocked(self) -> None:
        result = check_path_safety_for_auto_edit("/project/package-lock.json")
        self.assertIsNotNone(result)


class TestPermissionRuleFlow(unittest.TestCase):
    """Permission rules: parse → match → roundtrip."""

    def test_simple_tool_rule(self) -> None:
        rule = permission_rule_value_from_string("Bash")
        self.assertEqual(rule.tool_name, "Bash")
        self.assertIsNone(rule.rule_content)

    def test_content_rule(self) -> None:
        rule = permission_rule_value_from_string("Read(src/*.py)")
        self.assertEqual(rule.tool_name, "Read")
        self.assertEqual(rule.rule_content, "src/*.py")

    def test_roundtrip(self) -> None:
        original = "Write(src/app.py)"
        rule = permission_rule_value_from_string(original)
        serialized = permission_rule_value_to_string(rule)
        self.assertEqual(serialized, original)

    def test_legacy_alias_normalization(self) -> None:
        rule = permission_rule_value_from_string("Task")
        self.assertEqual(rule.tool_name, "Agent")

    def test_escaped_content(self) -> None:
        rule_str = "Bash(echo \\(hello\\))"
        rule = permission_rule_value_from_string(rule_str)
        self.assertEqual(rule.tool_name, "Bash")
        self.assertEqual(rule.rule_content, "echo (hello)")


class TestDangerousBashPermissionDetection(unittest.TestCase):
    """Dangerous bash permission patterns."""

    def test_wildcard_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", None))

    def test_code_execution_dangerous(self) -> None:
        for cmd in ["python", "node", "ruby", "perl", "bash"]:
            self.assertTrue(
                is_dangerous_bash_permission("Bash", cmd),
                f"Expected dangerous for: {cmd}",
            )

    def test_privilege_escalation_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "sudo"))

    def test_safe_commands_not_dangerous(self) -> None:
        for cmd in ["ls -la", "cat file.txt", "grep pattern", "echo hello"]:
            self.assertFalse(
                is_dangerous_bash_permission("Bash", cmd),
                f"Expected safe for: {cmd}",
            )

    def test_non_bash_not_dangerous(self) -> None:
        self.assertFalse(is_dangerous_bash_permission("Read", None))
        self.assertFalse(is_dangerous_bash_permission("Write", "*"))


if __name__ == "__main__":
    unittest.main()
