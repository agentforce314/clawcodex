from __future__ import annotations

import unittest

from src.permissions.bash_security import (
    DANGEROUS_BASH_PATTERNS,
    is_dangerous_bash_permission,
)


class TestDangerousBashPatterns(unittest.TestCase):
    def test_dangerous_patterns_list_not_empty(self) -> None:
        self.assertGreater(len(DANGEROUS_BASH_PATTERNS), 0)

    def test_contains_python(self) -> None:
        self.assertIn("python", DANGEROUS_BASH_PATTERNS)

    def test_contains_node(self) -> None:
        self.assertIn("node", DANGEROUS_BASH_PATTERNS)

    def test_contains_eval(self) -> None:
        self.assertIn("eval", DANGEROUS_BASH_PATTERNS)

    def test_contains_sudo(self) -> None:
        self.assertIn("sudo", DANGEROUS_BASH_PATTERNS)


class TestIsDangerousBashPermission(unittest.TestCase):
    def test_non_bash_tool_not_dangerous(self) -> None:
        self.assertFalse(is_dangerous_bash_permission("Write", None))

    def test_bash_with_no_content_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", None))

    def test_bash_with_empty_content_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", ""))

    def test_bash_with_wildcard_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "*"))

    def test_bash_with_python_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "python"))

    def test_bash_with_python_prefix_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "python script.py"))

    def test_bash_with_node_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "node"))

    def test_bash_with_eval_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "eval"))

    def test_bash_with_ssh_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "ssh"))

    def test_bash_with_npm_run_is_dangerous(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "npm run build"))

    def test_bash_with_safe_command_not_dangerous(self) -> None:
        self.assertFalse(is_dangerous_bash_permission("Bash", "ls -la"))

    def test_bash_with_git_not_dangerous(self) -> None:
        self.assertFalse(is_dangerous_bash_permission("Bash", "git status"))

    def test_bash_with_cat_not_dangerous(self) -> None:
        self.assertFalse(is_dangerous_bash_permission("Bash", "cat file.txt"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(is_dangerous_bash_permission("Bash", "Python"))
        self.assertTrue(is_dangerous_bash_permission("Bash", "NODE"))


if __name__ == "__main__":
    unittest.main()
