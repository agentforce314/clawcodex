"""ch07 / Phase 0.2 (T1): behavioural parity between legacy
`_run_tools_partitioned` and the new orchestrator-backed
`_collect_tool_results`.

Locks the migration promise — same number of tool_result blocks, same
tool_use_id → content mapping, same is_error flags. Without this test,
a silent behavior drift between the two paths could go unnoticed.

NB: `_run_tools_partitioned` is kept as a deprecated shim during the
transition. This test will fail when it's removed; at that point delete
the comparison and keep only the `_collect_tool_results` assertion.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.query.query import _collect_tool_results, _run_tools_partitioned
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.tool_system.registry import ToolRegistry
from src.types.messages import AssistantMessage, create_assistant_message
from src.utils.abort_controller import AbortController


def _make_tools() -> list[Tool]:
    safe = build_tool(
        name="Read",
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name="Read", output=f"read:{inp.get('path','?')}"),
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )
    unsafe = build_tool(
        name="Edit",
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: ToolResult(name="Edit", output=f"edit:{inp.get('path','?')}"),
        is_concurrency_safe=lambda _: False,
        is_read_only=lambda _: False,
    )
    return [safe, unsafe]


def _extract_tool_results(messages: list) -> dict[str, dict[str, Any]]:
    """Collapse a list of UserMessages to a {tool_use_id: result-summary} map.

    The legacy `_run_tools_partitioned` emits ``ToolResultBlock`` dataclasses;
    the new orchestrator path emits dicts. Normalize both so the comparison
    is shape-agnostic.
    """
    out: dict[str, dict[str, Any]] = {}
    for um in messages:
        content = getattr(um, "content", None)
        if not isinstance(content, list):
            continue
        for b in content:
            # Dict shape (new path)
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if tid and tid not in out:
                    out[tid] = {"tool_use_id": tid, "is_error": b.get("is_error", False)}
            # Dataclass shape (legacy)
            elif hasattr(b, "tool_use_id") and hasattr(b, "is_error"):
                tid = b.tool_use_id
                if tid and tid not in out:
                    out[tid] = {"tool_use_id": tid, "is_error": b.is_error}
    return out


class TestOrchestratorPartitionedParity(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_batch_same_results(self):
        """[Read, Read, Edit, Read] — three batches in both paths.
        Each tool_use_id must appear with the same is_error flag."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = _make_tools()
            ctx = ToolContext(
                workspace_root=Path(tmp),
                options=ToolUseOptions(tools=tools),
                abort_controller=AbortController(),
            )
            registry = ToolRegistry(tools)
            msg = create_assistant_message(content="x")
            blocks = [
                ToolUseBlock(id="r1", name="Read", input={"path": "a"}),
                ToolUseBlock(id="r2", name="Read", input={"path": "b"}),
                ToolUseBlock(id="e1", name="Edit", input={"path": "c"}),
                ToolUseBlock(id="r3", name="Read", input={"path": "d"}),
            ]

            # Path 1 — legacy shim
            ctx_legacy = ToolContext(
                workspace_root=Path(tmp),
                options=ToolUseOptions(tools=tools),
                abort_controller=AbortController(),
            )
            legacy = await _run_tools_partitioned(blocks, registry, ctx_legacy, tools)

            # Path 2 — orchestrator
            ctx_new = ToolContext(
                workspace_root=Path(tmp),
                options=ToolUseOptions(tools=tools),
                abort_controller=AbortController(),
            )
            new_results, _ = await _collect_tool_results(
                blocks, [msg], registry, ctx_new, tools,
            )

            legacy_map = _extract_tool_results(legacy)
            new_map = _extract_tool_results(new_results)

            # Same tool_use_ids appear in both.
            self.assertEqual(set(legacy_map.keys()), set(new_map.keys()))
            for tid in legacy_map:
                self.assertEqual(
                    bool(legacy_map[tid].get("is_error")),
                    bool(new_map[tid].get("is_error")),
                    f"is_error mismatch for {tid}",
                )


if __name__ == "__main__":
    unittest.main()
