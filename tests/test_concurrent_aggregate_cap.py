"""ch07 / Phase 2b: per-message 200K aggregate cap survives concurrent
dispatch through ``run_tool_use``.

The orchestrator's ``_run_tools_concurrently`` (after Phase 2c) gives
each tool a ``dataclasses.replace`` copy of the context. Phase 2a.5
moved the counter+lock into a shared ``AggregateBudget`` reference so
the replace copies all share the same counter; Phase 2b wrapped the
read-decide-write in ``tool_execution.py:316–333`` with the same lock.
Together these prevent the cap from being silently bypassed.

Without Phase 2a.5 + 2b, this test fails: 10 concurrent tools each
emit a ~30K block (30K * 10 = 300K total, above the 200K cap). With
the fixes, at least one block must be persisted (its content wrapped
into the "(persisted to disk)" placeholder).
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from src.services.tool_execution.orchestrator import run_tools
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import create_assistant_message
from src.utils.abort_controller import AbortController


def _allow_all(_tool, tool_input, _ctx, _msg, _id, force_decision=None):
    return {"behavior": "allow", "updatedInput": tool_input}


def _make_context(tools, tmp_path):
    return ToolContext(
        workspace_root=Path(tmp_path),
        options=ToolUseOptions(tools=tools),
        abort_controller=AbortController(),
    )


class TestConcurrentAggregateCap(unittest.IsolatedAsyncioTestCase):
    async def test_200k_cap_enforced_across_concurrent_batch(self):
        """Twenty concurrent tools each emit 15K of output. Each block
        is under its per-tool 20K threshold, so per-tool persistence
        does NOT fire — only the per-MESSAGE 200K aggregate cap can
        cause persistence. With Phase 2a.5 + 2b, the shared counter
        accumulates across the batch and tools whose addition would
        cross 200K are persisted. Without the fixes, each per-tool
        context starts at counter=0, all 20 see "under cap," and the
        message-level aggregate is silently 300K."""
        block_size = 15_000  # under per-tool 20K threshold

        def _big_call(_inp, _ctx):
            return ToolResult(name="Big", output="x" * block_size)

        tool = build_tool(
            name="Big",
            input_schema={"type": "object", "properties": {}},
            call=_big_call,
            is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
            max_result_size_chars=20_000,  # 15K block ≪ 20K threshold
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _make_context([tool], tmp)
            msg = create_assistant_message(content="x")
            num_blocks = 20
            blocks = [
                ToolUseBlock(id=f"big_{i}", name="Big", input={})
                for i in range(num_blocks)
            ]

            user_msgs = []
            async for update in run_tools(blocks, [msg], _allow_all, ctx):
                if update.message is not None:
                    user_msgs.append(update.message)

            persisted = 0
            full_size_blocks = 0
            for um in user_msgs:
                content = getattr(um, "content", None)
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_result":
                        continue
                    content_str = str(block.get("content", ""))
                    if "persisted-output" in content_str or "persisted to disk" in content_str.lower():
                        persisted += 1
                    elif len(content_str) >= block_size - 100:
                        full_size_blocks += 1

            # 20 × 15K = 300K. After ~13 blocks the running aggregate
            # crosses 200K, so the remaining ~7 blocks must be persisted.
            # Lower bound: at least 1 persisted block proves the cap
            # is enforced. Without Phase 2a.5+2b, persisted == 0.
            self.assertGreaterEqual(
                persisted, 1,
                f"no block was persisted (full={full_size_blocks}, persisted={persisted}) — "
                "the aggregate cap was bypassed under concurrent dispatch",
            )

            # Aggregate counter accumulates across the batch. With per-
            # tool isolation (Phase 2c) but NO shared budget (Phase 2a.5
            # reverted), each per-tool context's counter would write to
            # its own scalar and the parent's counter would stay at 0.
            self.assertGreaterEqual(
                ctx.tool_result_chars_so_far, 100_000,
                f"parent counter is {ctx.tool_result_chars_so_far} (≪ expected ~150K-200K) — "
                "per-tool contexts have isolated counters (Phase 2a.5 reverted?)",
            )


if __name__ == "__main__":
    unittest.main()
