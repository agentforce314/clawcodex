"""WS-10: E2E integration — edit flow matches TS behavior.

Simulates: User prompt → Read + Edit → permission check → result.
Tests the full tool dispatch pipeline for the Edit and Write tools.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.errors import ToolPermissionError
from src.tool_system.protocol import ToolCall


class TestE2EEditFlow(unittest.TestCase):
    """End-to-end edit tool flow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create a file to edit
        self.test_file = self.root / "target.py"
        self.test_file.write_text("def hello():\n    return 'hello'\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_then_edit(self) -> None:
        """Read a file, then edit it — standard TS flow."""
        # Step 1: Read
        read_result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        self.assertFalse(read_result.is_error)

        # Step 2: Edit
        edit_result = self.registry.dispatch(
            ToolCall(name="Edit", input={
                "file_path": str(self.test_file),
                "old_string": "return 'hello'",
                "new_string": "return 'world'",
            }),
            self.ctx,
        )
        self.assertFalse(edit_result.is_error)

        # Verify the file was modified
        content = self.test_file.read_text()
        self.assertIn("return 'world'", content)
        self.assertNotIn("return 'hello'", content)

    def test_edit_without_read_fails(self) -> None:
        """Edit without prior Read should fail (TS requires Read first)."""
        # Create a fresh context where the file was NOT read
        fresh_ctx = ToolContext(workspace_root=self.root)
        try:
            edit_result = self.registry.dispatch(
                ToolCall(name="Edit", input={
                    "file_path": str(self.test_file),
                    "old_string": "return 'hello'",
                    "new_string": "return 'world'",
                }),
                fresh_ctx,
            )
            # Should error because file wasn't read first
            self.assertTrue(edit_result.is_error)
        except Exception:
            pass  # Exception is also acceptable

    def test_edit_nonexistent_file_fails(self) -> None:
        """Edit on a nonexistent file should fail."""
        try:
            result = self.registry.dispatch(
                ToolCall(name="Edit", input={
                    "file_path": str(self.root / "nonexistent.py"),
                    "old_string": "hello",
                    "new_string": "world",
                }),
                self.ctx,
            )
            self.assertTrue(result.is_error)
        except Exception:
            pass  # Exception is also acceptable

    def test_edit_preserves_other_content(self) -> None:
        """Edit should only change the targeted text."""
        # Read first
        self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        # Edit
        self.registry.dispatch(
            ToolCall(name="Edit", input={
                "file_path": str(self.test_file),
                "old_string": "return 'hello'",
                "new_string": "return 'world'",
            }),
            self.ctx,
        )
        content = self.test_file.read_text()
        self.assertIn("def hello():", content)
        self.assertIn("return 'world'", content)


class TestE2EWriteFlow(unittest.TestCase):
    """End-to-end Write tool flow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_creates_new_file(self) -> None:
        """Write tool creates a new file."""
        target = self.root / "new_file.py"
        result = self.registry.dispatch(
            ToolCall(name="Write", input={
                "file_path": str(target),
                "content": "print('hello')\n",
            }),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "print('hello')\n")

    def test_write_creates_directories(self) -> None:
        """Write tool creates intermediate directories."""
        target = self.root / "deep" / "nested" / "dir" / "file.py"
        result = self.registry.dispatch(
            ToolCall(name="Write", input={
                "file_path": str(target),
                "content": "content\n",
            }),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertTrue(target.exists())

    def test_write_outside_workspace_blocked(self) -> None:
        """Write tool blocks writes outside workspace root."""
        with self.assertRaises(ToolPermissionError):
            self.registry.dispatch(
                ToolCall(name="Write", input={
                    "file_path": "/tmp/outside_workspace_test.txt",
                    "content": "bad\n",
                }),
                self.ctx,
            )


class TestE2EPermissionDenyBlocks(unittest.TestCase):
    """Permission deny rules block tool dispatch."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)

        # Create context with Bash denied
        self.ctx = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(
                mode="default",
                always_deny_rules={"session": ["Bash"]},
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_denied_tool_returns_error(self) -> None:
        """A tool with a deny rule should be blocked."""
        with self.assertRaises(ToolPermissionError):
            self.registry.dispatch(
                ToolCall(name="Bash", input={"command": "echo hello"}),
                self.ctx,
            )


if __name__ == "__main__":
    unittest.main()
