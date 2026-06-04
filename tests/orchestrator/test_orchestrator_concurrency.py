"""Step 2/3/4a/8 — orchestrator concurrency invariants (G1, G2, G4, G17).

Covers the gaps the chapter calls out as the safety contract for
``run_tools`` / ``partition_tool_calls``:

- G1: an exception escaping ``run_tool_use`` in a concurrent batch must
  produce a synthetic ``tool_use_error`` so the next API turn doesn't
  see an unmatched ``tool_use`` block.
- G2: ``classify_concurrency_safe`` is the single fail-closed barrier
  for "is this safe to parallelize?" — must reject unknown tools,
  non-dict input, and exceptions from the per-tool classifier.
- G4: context modifiers from concurrent-safe tools are accepted as
  ``ContextModifier`` dataclasses (the producer's actual shape) and
  applied in tool-submission order after the batch finishes.
- G17: result ordering matches submission order even when tools
  complete out of order.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from src.services.tool_execution.orchestrator import (
    classify_concurrency_safe,
    partition_tool_calls,
    run_tools,
)
from src.services.tool_execution.streaming_executor import ToolUseBlock
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import create_assistant_message
from src.utils.abort_controller import AbortController


def _allow_all(_tool, tool_input, _ctx, _msg, _id):
    return {"behavior": "allow", "updatedInput": tool_input}


def _make_context(tools):
    return ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools),
        abort_controller=AbortController(),
    )


# ---------------------------------------------------------------------------
# G2 — classify_concurrency_safe is the single fail-closed barrier
# ---------------------------------------------------------------------------


class TestClassifyConcurrencySafe(unittest.TestCase):
    def test_unknown_tool_is_serial(self):
        self.assertFalse(classify_concurrency_safe(None, {}))

    def test_non_dict_input_is_serial(self):
        # The fail-closed input-shape barrier — TS calls
        # `inputSchema.safeParse` here; we can't always validate the
        # full schema, but anything that isn't even a dict is rejected.
        tool = build_tool(
            name="X", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="X", output=""),
            is_concurrency_safe=lambda _: True,
        )
        self.assertFalse(classify_concurrency_safe(tool, None))
        self.assertFalse(classify_concurrency_safe(tool, "not a dict"))
        self.assertFalse(classify_concurrency_safe(tool, [1, 2, 3]))

    def test_classifier_exception_is_serial(self):
        def boom(_):
            raise RuntimeError("classifier crashed")

        tool = build_tool(
            name="X", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="X", output=""),
            is_concurrency_safe=boom,
        )
        # Mirror TS: shell-quote crash on a malformed Bash command must
        # not raise out of partition.
        self.assertFalse(classify_concurrency_safe(tool, {"command": "ls"}))

    def test_classifier_true_passes_through(self):
        tool = build_tool(
            name="X", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="X", output=""),
            is_concurrency_safe=lambda _: True,
        )
        self.assertTrue(classify_concurrency_safe(tool, {}))


# ---------------------------------------------------------------------------
# G1 — concurrent batch surfaces escaped exceptions as tool_use_error
# ---------------------------------------------------------------------------


class TestConcurrentExceptionsNotSwallowed(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_error_in_call_yields_error_message(self):
        def boom(_inp, _ctx):
            raise RuntimeError("boom from inside call")

        tool = build_tool(
            name="Boom", input_schema={"type": "object", "properties": {}},
            call=boom, is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
        )
        ctx = _make_context([tool])
        msg = create_assistant_message(content="x")
        block = ToolUseBlock(id="t1", name="Boom", input={})

        results = []
        async for upd in run_tools([block], [msg], _allow_all, ctx):
            if upd.message is not None:
                results.append(upd.message)

        # We must see at least one user message with an error tool_result
        # for tool_use_id t1. Without G1 the runtime error was silently
        # dropped and the result list contained nothing.
        error_blocks = [
            b for r in results
            if hasattr(r, "content") and isinstance(r.content, list)
            for b in r.content
            if isinstance(b, dict) and b.get("type") == "tool_result"
            and b.get("tool_use_id") == "t1"
        ]
        self.assertTrue(error_blocks, "no tool_result emitted for failing tool")
        self.assertTrue(any(b.get("is_error") for b in error_blocks))


# ---------------------------------------------------------------------------
# G4 — context modifier applied in submission order after concurrent batch
# ---------------------------------------------------------------------------


class TestContextModifierApplication(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_context_modifier_applied_after_batch(self):
        applied: list[str] = []

        def make_modifier(label: str):
            def modify(ctx):
                applied.append(label)
                return ctx
            return modify

        def call_with_modifier(label: str):
            def _call(_inp, _ctx):
                return ToolResult(
                    name=label, output="ok",
                    context_modifier=make_modifier(label),
                )
            return _call

        tools = [
            build_tool(
                name=f"T{i}",
                input_schema={"type": "object", "properties": {}},
                call=call_with_modifier(f"T{i}"),
                is_concurrency_safe=lambda _: True,
                is_read_only=lambda _: True,
            )
            for i in range(3)
        ]
        ctx = _make_context(tools)
        msg = create_assistant_message(content="x")
        blocks = [ToolUseBlock(id=f"t{i}", name=f"T{i}", input={}) for i in range(3)]

        async for _ in run_tools(blocks, [msg], _allow_all, ctx):
            pass

        # All three modifiers fired, and they ran in submission order
        # (T0, T1, T2) regardless of completion order. This is the
        # concurrent-batch contract: modifiers are queued and applied
        # at the batch boundary, not as tools complete.
        self.assertEqual(applied, ["T0", "T1", "T2"])


# ---------------------------------------------------------------------------
# G17 — submission-order result yielding for concurrent batches
# ---------------------------------------------------------------------------


class TestSubmissionOrderInvariant(unittest.IsolatedAsyncioTestCase):
    async def test_partition_groups_consecutive_safe_tools(self):
        """Sanity: partition_tool_calls produces the same shape as TS."""
        safe = build_tool(
            name="Safe", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="Safe", output=""),
            is_concurrency_safe=lambda _: True,
        )
        unsafe = build_tool(
            name="Unsafe", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="Unsafe", output=""),
            is_concurrency_safe=lambda _: False,
        )
        ctx = _make_context([safe, unsafe])
        seq = [
            ToolUseBlock(id="1", name="Safe", input={}),
            ToolUseBlock(id="2", name="Safe", input={}),
            ToolUseBlock(id="3", name="Unsafe", input={}),
            ToolUseBlock(id="4", name="Safe", input={}),
        ]
        batches = partition_tool_calls(seq, ctx)
        # Three batches: {safe×2}, {unsafe×1}, {safe×1}
        self.assertEqual(
            [(b.is_concurrency_safe, len(b.blocks)) for b in batches],
            [(True, 2), (False, 1), (True, 1)],
        )

    async def test_results_arrive_for_all_tools_even_with_skewed_durations(self):
        """A slow tool first, fast tools after — every tool still gets
        a tool_result. We don't pin the exact yield order in this test
        (the orchestrator path interleaves), but we assert no tool is
        dropped — which was the failure mode of swallowed exceptions
        and the proper completion-tracking that the fixes preserve."""

        async def slow_call(_inp, _ctx):
            await asyncio.sleep(0.02)
            return ToolResult(name="Slow", output="slow")

        slow = build_tool(
            name="Slow", input_schema={"type": "object", "properties": {}},
            call=slow_call, is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
        )
        fast = build_tool(
            name="Fast", input_schema={"type": "object", "properties": {}},
            call=lambda i, c: ToolResult(name="Fast", output="fast"),
            is_concurrency_safe=lambda _: True,
            is_read_only=lambda _: True,
        )
        ctx = _make_context([slow, fast])
        msg = create_assistant_message(content="x")
        blocks = [
            ToolUseBlock(id="slow", name="Slow", input={}),
            ToolUseBlock(id="f1", name="Fast", input={}),
            ToolUseBlock(id="f2", name="Fast", input={}),
        ]

        ids_seen: set[str] = set()
        async for upd in run_tools(blocks, [msg], _allow_all, ctx):
            if upd.message is None or not hasattr(upd.message, "content"):
                continue
            content = upd.message.content
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    if tid:
                        ids_seen.add(tid)

        # All three submitted ids saw a tool_result. Without G1 the
        # slow tool's result might race the fast tools' completion.
        self.assertEqual(ids_seen, {"slow", "f1", "f2"})


if __name__ == "__main__":
    unittest.main()
