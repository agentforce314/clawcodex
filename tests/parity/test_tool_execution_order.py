"""WS-10: Behavioral parity — tool execution order and batching matches TS.

Verifies:
- partition_tool_calls produces same batch structure as TS
- Concurrent-safe tools batch together
- Non-concurrent tools are isolated
- Execution order is preserved (FIFO)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.services.tool_execution.orchestrator import (
    Batch,
    partition_tool_calls,
)
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext


def _make_tool(name: str, *, concurrent: bool = False) -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: None,
        is_concurrency_safe=lambda _: concurrent,
        is_read_only=lambda _: concurrent,
    )


def _make_context(*tools: Tool) -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.options = MagicMock()
    ctx.options.tools = list(tools)
    return ctx


def _make_block(tool_id: str, name: str) -> ToolUseBlock:
    return ToolUseBlock(id=tool_id, name=name, input={})


class TestPartitionToolCalls(unittest.TestCase):
    """partition_tool_calls batching matches TS behavior."""

    def test_single_non_concurrent_tool(self) -> None:
        tools = [_make_tool("Edit")]
        ctx = _make_context(*tools)
        blocks = [_make_block("1", "Edit")]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 1)
        self.assertFalse(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 1)

    def test_single_concurrent_tool(self) -> None:
        tools = [_make_tool("Read", concurrent=True)]
        ctx = _make_context(*tools)
        blocks = [_make_block("1", "Read")]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)

    def test_consecutive_concurrent_tools_batch_together(self) -> None:
        """Two consecutive concurrent-safe tools should be in one batch."""
        tools = [
            _make_tool("Read", concurrent=True),
            _make_tool("Glob", concurrent=True),
        ]
        ctx = _make_context(*tools)
        blocks = [
            _make_block("1", "Read"),
            _make_block("2", "Glob"),
        ]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 2)

    def test_non_concurrent_breaks_batch(self) -> None:
        """A non-concurrent tool breaks the concurrent batch."""
        tools = [
            _make_tool("Read", concurrent=True),
            _make_tool("Edit"),
            _make_tool("Grep", concurrent=True),
        ]
        ctx = _make_context(*tools)
        blocks = [
            _make_block("1", "Read"),
            _make_block("2", "Edit"),
            _make_block("3", "Grep"),
        ]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 3)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertFalse(batches[1].is_concurrency_safe)
        self.assertTrue(batches[2].is_concurrency_safe)

    def test_consecutive_non_concurrent_separate(self) -> None:
        """Each non-concurrent tool gets its own batch (TS behavior)."""
        tools = [
            _make_tool("Edit"),
            _make_tool("Write"),
        ]
        ctx = _make_context(*tools)
        blocks = [
            _make_block("1", "Edit"),
            _make_block("2", "Write"),
        ]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 2)
        self.assertFalse(batches[0].is_concurrency_safe)
        self.assertFalse(batches[1].is_concurrency_safe)

    def test_many_concurrent_tools_single_batch(self) -> None:
        """Many concurrent tools should all go in one batch."""
        tools = [_make_tool(f"Tool{i}", concurrent=True) for i in range(5)]
        ctx = _make_context(*tools)
        blocks = [_make_block(str(i), f"Tool{i}") for i in range(5)]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual(len(batches[0].blocks), 5)

    def test_mixed_sequence_preserves_order(self) -> None:
        """Batch order matches input order — FIFO."""
        tools = [
            _make_tool("Read", concurrent=True),
            _make_tool("Glob", concurrent=True),
            _make_tool("Edit"),
            _make_tool("Grep", concurrent=True),
            _make_tool("Write"),
        ]
        ctx = _make_context(*tools)
        blocks = [
            _make_block("1", "Read"),
            _make_block("2", "Glob"),
            _make_block("3", "Edit"),
            _make_block("4", "Grep"),
            _make_block("5", "Write"),
        ]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 4)
        self.assertTrue(batches[0].is_concurrency_safe)
        self.assertEqual([b.name for b in batches[0].blocks], ["Read", "Glob"])
        self.assertFalse(batches[1].is_concurrency_safe)
        self.assertEqual([b.name for b in batches[1].blocks], ["Edit"])
        self.assertTrue(batches[2].is_concurrency_safe)
        self.assertEqual([b.name for b in batches[2].blocks], ["Grep"])
        self.assertFalse(batches[3].is_concurrency_safe)
        self.assertEqual([b.name for b in batches[3].blocks], ["Write"])

    def test_unknown_tool_treated_as_non_concurrent(self) -> None:
        """Tool not in definitions defaults to non-concurrent."""
        ctx = _make_context()  # No tools registered
        blocks = [_make_block("1", "UnknownTool")]
        batches = partition_tool_calls(blocks, ctx)
        self.assertEqual(len(batches), 1)
        self.assertFalse(batches[0].is_concurrency_safe)

    def test_empty_list_returns_empty(self) -> None:
        ctx = _make_context()
        batches = partition_tool_calls([], ctx)
        self.assertEqual(len(batches), 0)


class TestBatchDataclass(unittest.TestCase):
    """Batch dataclass has correct structure."""

    def test_batch_has_blocks(self) -> None:
        b = Batch(is_concurrency_safe=True, blocks=[])
        self.assertIsInstance(b.blocks, list)

    def test_batch_has_concurrency_flag(self) -> None:
        b = Batch(is_concurrency_safe=False, blocks=[])
        self.assertFalse(b.is_concurrency_safe)


if __name__ == "__main__":
    unittest.main()
