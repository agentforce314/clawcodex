from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.permissions.filesystem import (
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    check_path_safety_for_auto_edit,
    check_read_permission_for_path,
    check_write_permission_for_path,
    normalize_case_for_comparison,
)


class TestDangerousLists(unittest.TestCase):
    def test_dangerous_files_not_empty(self) -> None:
        self.assertGreater(len(DANGEROUS_FILES), 0)

    def test_dangerous_directories_not_empty(self) -> None:
        self.assertGreater(len(DANGEROUS_DIRECTORIES), 0)

    def test_gitconfig_in_dangerous_files(self) -> None:
        self.assertIn(".gitconfig", DANGEROUS_FILES)

    def test_bashrc_in_dangerous_files(self) -> None:
        self.assertIn(".bashrc", DANGEROUS_FILES)

    def test_zshrc_in_dangerous_files(self) -> None:
        self.assertIn(".zshrc", DANGEROUS_FILES)

    def test_git_in_dangerous_directories(self) -> None:
        self.assertIn(".git", DANGEROUS_DIRECTORIES)

    def test_vscode_in_dangerous_directories(self) -> None:
        self.assertIn(".vscode", DANGEROUS_DIRECTORIES)

    def test_claude_in_dangerous_directories(self) -> None:
        self.assertIn(".claude", DANGEROUS_DIRECTORIES)


class TestNormalizeCaseForComparison(unittest.TestCase):
    def test_lowercases_path(self) -> None:
        self.assertEqual(normalize_case_for_comparison("/FOO/BAR"), "/foo/bar")


class TestCheckPathSafetyForAutoEdit(unittest.TestCase):
    def test_regular_file_allowed(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/project/src/main.py")
        self.assertIsNone(result)

    def test_gitconfig_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/.gitconfig")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_bashrc_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/.bashrc")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_git_directory_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.git/config")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_vscode_directory_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.vscode/settings.json")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_claude_directory_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.claude/settings.json")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_case_insensitive_dangerous_file(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/.BASHRC")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_case_insensitive_dangerous_dir(self) -> None:
        result = check_path_safety_for_auto_edit("/project/.GIT/config")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_mcp_json_needs_ask(self) -> None:
        result = check_path_safety_for_auto_edit("/home/user/.mcp.json")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")


class TestCheckReadPermissionForPath(unittest.TestCase):
    def test_regular_path_allowed(self) -> None:
        result = check_read_permission_for_path("/home/user/file.txt")
        self.assertIsNone(result)

    def test_within_allowed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "file.txt")
            result = check_read_permission_for_path(f, allowed_directories=[tmp])
            self.assertIsNone(result)

    def test_outside_allowed_directory_passthrough(self) -> None:
        result = check_read_permission_for_path(
            "/other/file.txt",
            allowed_directories=["/home/user"],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "passthrough")


class TestCheckWritePermissionForPath(unittest.TestCase):
    def test_regular_path_allowed(self) -> None:
        result = check_write_permission_for_path("/home/user/project/main.py")
        self.assertIsNone(result)

    def test_dangerous_file_asks(self) -> None:
        result = check_write_permission_for_path("/home/user/.bashrc")
        self.assertIsNotNone(result)
        self.assertEqual(result.behavior, "ask")

    def test_outside_allowed_dir_passthrough(self) -> None:
        result = check_write_permission_for_path(
            "/other/file.txt",
            allowed_directories=["/home/user"],
        )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
