"""WS-10: E2E integration — multi-tool concurrent dispatch matches TS.

Simulates: Multiple concurrent-safe tools batched in parallel.
Tests partition_tool_calls + run_tools orchestration with real tools.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.services.tool_execution.orchestrator import (
    partition_tool_calls,
)
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall


class TestE2EMultiToolBatch(unittest.TestCase):
    """Multiple tools dispatched in sequence with correct results."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create test files
        (self.root / "a.txt").write_text("alpha\n")
        (self.root / "b.txt").write_text("bravo\n")
        (self.root / "c.py").write_text("def charlie(): pass\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_multiple_reads_all_succeed(self) -> None:
        """Multiple Read dispatches all return content."""
        files = ["a.txt", "b.txt", "c.py"]
        results = []
        for f in files:
            result = self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(self.root / f)}),
                self.ctx,
            )
            results.append(result)

        for result in results:
            self.assertFalse(result.is_error)

        self.assertIn("alpha", str(results[0].output))
        self.assertIn("bravo", str(results[1].output))
        self.assertIn("charlie", str(results[2].output))

    def test_read_then_glob_then_grep(self) -> None:
        """Sequential read → glob → grep all succeed."""
        # Read
        read_result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.root / "c.py")}),
            self.ctx,
        )
        self.assertFalse(read_result.is_error)

        # Glob
        glob_result = self.registry.dispatch(
            ToolCall(name="Glob", input={"pattern": "*.txt"}),
            self.ctx,
        )
        self.assertFalse(glob_result.is_error)

        # Grep
        grep_result = self.registry.dispatch(
            ToolCall(name="Grep", input={"pattern": "alpha", "path": str(self.root)}),
            self.ctx,
        )
        self.assertFalse(grep_result.is_error)


class TestE2EMultiToolPartition(unittest.TestCase):
    """Partition behavior with real registry tools."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)
        self.ctx.options.tools = self.registry.list_tools()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_concurrent_reads_batch_together(self) -> None:
        """Multiple Read tools should batch into one concurrent batch."""
        blocks = [
            ToolUseBlock(id="1", name="Read", input={"file_path": "/tmp/a"}),
            ToolUseBlock(id="2", name="Read", input={"file_path": "/tmp/b"}),
            ToolUseBlock(id="3", name="Read", input={"file_path": "/tmp/c"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 3)

    def test_read_edit_read_makes_three_batches(self) -> None:
        """Read + Edit + Read should make 3 batches: concurrent, exclusive, concurrent."""
        blocks = [
            ToolUseBlock(id="1", name="Read", input={"file_path": "/tmp/a"}),
            ToolUseBlock(id="2", name="Edit", input={"file_path": "/tmp/a"}),
            ToolUseBlock(id="3", name="Read", input={"file_path": "/tmp/b"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertEqual(len(batches), 3)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertFalse(batches[1].is_concurrency_safe)
        self.assertTrue(batches[2].is_concurrency_safe)

    def test_glob_grep_read_all_concurrent(self) -> None:
        """Glob + Grep + Read are all concurrent-safe and batch together."""
        blocks = [
            ToolUseBlock(id="1", name="Glob", input={"pattern": "*"}),
            ToolUseBlock(id="2", name="Grep", input={"pattern": "test"}),
            ToolUseBlock(id="3", name="Read", input={"file_path": "/tmp/a"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 3)

    def test_write_breaks_concurrent_batch(self) -> None:
        """Write is not concurrent-safe and breaks batches."""
        blocks = [
            ToolUseBlock(id="1", name="Read", input={"file_path": "/tmp/a"}),
            ToolUseBlock(id="2", name="Write", input={"file_path": "/tmp/b"}),
            ToolUseBlock(id="3", name="Glob", input={"pattern": "*"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertEqual(len(batches), 3)

    def test_bash_readonly_is_concurrent(self) -> None:
        """Read-only Bash commands are concurrent-safe (matches TS isConcurrencySafe)."""
        blocks = [
            ToolUseBlock(id="1", name="Bash", input={"command": "echo hi"}),
            ToolUseBlock(id="2", name="Read", input={"file_path": "/tmp/a"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)

    def test_bash_destructive_not_concurrent(self) -> None:
        """Destructive Bash commands are not concurrent-safe."""
        blocks = [
            ToolUseBlock(id="1", name="Bash", input={"command": "python script.py"}),
            ToolUseBlock(id="2", name="Read", input={"file_path": "/tmp/a"}),
        ]
        batches = partition_tool_calls(blocks, self.ctx)
        self.assertGreaterEqual(len(batches), 2)
        self.assertFalse(batches[0].is_concurrency_safe)


class TestE2EToolSearchIntegration(unittest.TestCase):
    """ToolSearch integration with registry."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tool_search_finds_read(self) -> None:
        """ToolSearch can find the Read tool."""
        result = self.registry.dispatch(
            ToolCall(name="ToolSearch", input={"query": "select:Read"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIn("Read", result.output.get("matches", []))

    def test_tool_search_finds_edit(self) -> None:
        """ToolSearch can find the Edit tool."""
        result = self.registry.dispatch(
            ToolCall(name="ToolSearch", input={"query": "select:Edit"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIn("Edit", result.output.get("matches", []))


class TestE2ETodoWriteIntegration(unittest.TestCase):
    """TodoWrite integration — roundtrip behavior."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_todo_write_and_complete(self) -> None:
        """Write a todo, then complete it — matches TS TodoWrite behavior."""
        # Add todo
        result1 = self.registry.dispatch(
            ToolCall(name="TodoWrite", input={
                "todos": [{"content": "Task 1", "status": "pending", "activeForm": "Doing task 1"}],
            }),
            self.ctx,
        )
        self.assertFalse(result1.is_error)
        self.assertEqual(len(self.ctx.todos), 1)

        # Complete todo
        result2 = self.registry.dispatch(
            ToolCall(name="TodoWrite", input={
                "todos": [{"content": "Task 1", "status": "completed", "activeForm": "Done task 1"}],
            }),
            self.ctx,
        )
        self.assertFalse(result2.is_error)
        self.assertEqual(len(self.ctx.todos), 0)


if __name__ == "__main__":
    unittest.main()
