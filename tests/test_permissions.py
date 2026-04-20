from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.permissions.types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthroughResult,
    ToolPermissionContext,
)
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools.write import WriteTool
from src.tool_system.tools.edit import EditTool


class TestPermissionDecisionTypes(unittest.TestCase):
    def test_allow_decision(self) -> None:
        result = PermissionAllowDecision()
        self.assertEqual(result.behavior, "allow")
        self.assertIsNone(result.updated_input)

    def test_allow_decision_with_updated_input(self) -> None:
        updated = {"key": "value"}
        result = PermissionAllowDecision(updated_input=updated)
        self.assertEqual(result.updated_input, updated)

    def test_deny_decision(self) -> None:
        result = PermissionDenyDecision(message="test message")
        self.assertEqual(result.behavior, "deny")
        self.assertEqual(result.message, "test message")

    def test_ask_decision(self) -> None:
        result = PermissionAskDecision(message="test message")
        self.assertEqual(result.behavior, "ask")
        self.assertEqual(result.message, "test message")

    def test_passthrough_result(self) -> None:
        result = PermissionPassthroughResult(message="maybe")
        self.assertEqual(result.behavior, "passthrough")
        self.assertEqual(result.message, "maybe")


class TestWriteToolPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_check_permissions_passthrough_for_regular_file(self) -> None:
        result = WriteTool.check_permissions(
            {"file_path": str(self.root / "test.txt"), "content": "hello"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "passthrough")

    def test_check_permissions_ask_for_md_file_when_docs_disallowed(self) -> None:
        result = WriteTool.check_permissions(
            {"file_path": str(self.root / "test.md"), "content": "hello"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "ask")
        self.assertIn("allow_docs", result.message.lower())

    def test_check_permissions_passthrough_for_md_file_when_docs_allowed(self) -> None:
        self.ctx.allow_docs = True
        result = WriteTool.check_permissions(
            {"file_path": str(self.root / "test.md"), "content": "hello"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "passthrough")

    def test_check_permissions_ask_for_markdown_file(self) -> None:
        result = WriteTool.check_permissions(
            {"file_path": str(self.root / "test.markdown"), "content": "hello"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "ask")


class TestEditToolPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(workspace_root=self.root)
        self.test_file = self.root / "test.md"
        self.test_file.write_text("original content", encoding="utf-8")
        self.ctx.mark_file_read(self.test_file)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_check_permissions_passthrough_for_regular_file(self) -> None:
        result = EditTool.check_permissions(
            {"file_path": str(self.root / "test.txt"), "old_string": "a", "new_string": "b"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "passthrough")

    def test_check_permissions_ask_for_md_file_when_docs_disallowed(self) -> None:
        result = EditTool.check_permissions(
            {"file_path": str(self.test_file), "old_string": "original", "new_string": "modified"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "ask")
        self.assertIn("allow_docs", result.message.lower())

    def test_check_permissions_passthrough_for_md_file_when_docs_allowed(self) -> None:
        self.ctx.allow_docs = True
        result = EditTool.check_permissions(
            {"file_path": str(self.test_file), "old_string": "original", "new_string": "modified"},
            self.ctx,
        )
        self.assertEqual(result.behavior, "passthrough")


class TestToolRegistryDispatchPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(mode="default"),
        )
        self.registry = ToolRegistry([WriteTool])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dispatch_allows_regular_file_with_handler(self) -> None:
        self.ctx.permission_handler = lambda name, msg, sug: (True, False)
        result = self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": str(self.root / "test.txt"), "content": "hello"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output.get("type"), "create")

    def test_dispatch_denies_md_file_without_handler(self) -> None:
        result = self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": str(self.root / "test.md"), "content": "hello"}),
            self.ctx,
        )
        self.assertTrue(result.is_error)
        error_msg = result.output.get("error", "").lower()
        self.assertTrue(
            "permission" in error_msg or "allow_docs" in error_msg or "blocked" in error_msg or "denied" in error_msg,
            f"Expected permission-related error, got: {error_msg}",
        )

    def test_dispatch_calls_permission_handler_for_ask(self) -> None:
        call_count = 0
        captured_args = None

        def mock_handler(tool_name: str, message: str, suggestion: str | None):
            nonlocal call_count, captured_args
            call_count += 1
            captured_args = (tool_name, message, suggestion)
            return True, False

        self.ctx.permission_handler = mock_handler

        result = self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": str(self.root / "test.md"), "content": "hello"}),
            self.ctx,
        )

        self.assertEqual(call_count, 1)
        self.assertEqual(captured_args[0], "Write")
        self.assertFalse(result.is_error)

    def test_dispatch_respects_handler_deny(self) -> None:
        def mock_handler(tool_name: str, message: str, suggestion: str | None):
            return False, False

        self.ctx.permission_handler = mock_handler

        result = self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": str(self.root / "test.md"), "content": "hello"}),
            self.ctx,
        )

        self.assertTrue(result.is_error)
        self.assertIn("denied", result.output.get("error", "").lower())

    def test_dispatch_allows_after_handler_enables_setting(self) -> None:
        def mock_handler(tool_name: str, message: str, suggestion: str | None):
            self.ctx.allow_docs = True
            return True, False

        self.ctx.permission_handler = mock_handler

        result = self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": str(self.root / "test.md"), "content": "hello"}),
            self.ctx,
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output.get("type"), "create")


class TestToolContextAllowDocs(unittest.TestCase):
    def test_default_allow_docs_is_false(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        ctx = ToolContext(workspace_root=Path(tmp.name))
        self.assertFalse(ctx.allow_docs)
        tmp.cleanup()

    def test_allow_docs_can_be_set_true(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        ctx = ToolContext(workspace_root=Path(tmp.name), allow_docs=True)
        self.assertTrue(ctx.allow_docs)
        tmp.cleanup()

    def test_allow_docs_is_mutable(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        ctx = ToolContext(workspace_root=Path(tmp.name))
        self.assertFalse(ctx.allow_docs)
        ctx.allow_docs = True
        self.assertTrue(ctx.allow_docs)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
