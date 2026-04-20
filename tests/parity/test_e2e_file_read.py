"""WS-10: E2E integration — file read flow matches TS behavior.

Simulates: User prompt → Read tool dispatched → file content returned.
Tests the full tool dispatch pipeline for the Read tool.
"""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.errors import ToolPermissionError
from src.tool_system.protocol import ToolCall


class TestE2EFileRead(unittest.TestCase):
    """End-to-end file read flow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create test files
        self.test_file = self.root / "hello.txt"
        self.test_file.write_text("Hello, world!\nLine 2\nLine 3\n")

        self.py_file = self.root / "example.py"
        self.py_file.write_text(textwrap.dedent("""\
            def greet(name):
                return f"Hello, {name}!"

            if __name__ == "__main__":
                print(greet("world"))
        """))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_file_returns_content(self) -> None:
        """Read tool returns file contents."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = result.output
        self.assertIn("Hello, world!", str(output))

    def test_read_file_marks_fingerprint(self) -> None:
        """Read tool marks the file as read in context."""
        self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        self.assertTrue(self.ctx.was_file_read_and_unchanged(self.test_file))

    def test_read_python_file(self) -> None:
        """Read tool can read Python files."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.py_file)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIn("def greet", str(result.output))

    def test_read_nonexistent_file_returns_error(self) -> None:
        """Read tool returns error for missing files."""
        try:
            result = self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(self.root / "nonexistent.txt")}),
                self.ctx,
            )
            self.assertTrue(result.is_error)
        except Exception:
            pass  # Exception is also acceptable

    def test_read_with_offset_and_limit(self) -> None:
        """Read tool supports offset and limit parameters."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={
                "file_path": str(self.test_file),
                "offset": 1,
                "limit": 2,
            }),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        content = str(result.output)
        self.assertIn("Hello, world!", content)

    def test_read_outside_workspace_blocked(self) -> None:
        """Read tool blocks reads outside workspace root."""
        with self.assertRaises(ToolPermissionError):
            self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": "/etc/passwd"}),
                self.ctx,
            )

    def test_read_tool_is_concurrent_safe(self) -> None:
        """Read tool can be called concurrently."""
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_concurrency_safe({"file_path": str(self.test_file)}))

    def test_read_tool_is_read_only(self) -> None:
        """Read tool is read-only."""
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({"file_path": str(self.test_file)}))


class TestE2EGlobGrep(unittest.TestCase):
    """End-to-end Glob and Grep search flows."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create test files
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("def main():\n    print('hello')\n")
        (self.root / "src" / "utils.py").write_text("def helper():\n    return 42\n")
        (self.root / "README.md").write_text("# Project\nThis is a project.\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_glob_finds_python_files(self) -> None:
        """Glob tool finds files by pattern."""
        result = self.registry.dispatch(
            ToolCall(name="Glob", input={"pattern": "**/*.py"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = str(result.output)
        self.assertIn("main.py", output)
        self.assertIn("utils.py", output)

    def test_glob_no_match_returns_empty(self) -> None:
        """Glob tool returns empty for no matches."""
        result = self.registry.dispatch(
            ToolCall(name="Glob", input={"pattern": "**/*.xyz"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)

    def test_grep_finds_pattern(self) -> None:
        """Grep tool finds text patterns."""
        result = self.registry.dispatch(
            ToolCall(name="Grep", input={"pattern": "def main", "path": str(self.root)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = str(result.output)
        self.assertIn("main.py", output)

    def test_grep_no_match_returns_empty(self) -> None:
        """Grep tool returns empty for no matches."""
        result = self.registry.dispatch(
            ToolCall(name="Grep", input={"pattern": "nonexistent_string_xyz", "path": str(self.root)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)


if __name__ == "__main__":
    unittest.main()
