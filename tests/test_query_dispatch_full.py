"""Tests for Phase-3 ``query.py:_dispatch_single_tool`` refactor.

Verifies the production main-loop dispatch path now correctly:

* Propagates ``ToolResult.new_messages`` (Agent transcripts, etc.)
* Propagates ``ToolResult.context_modifier`` (EnterPlanMode, etc.)
* Returns the new ``(messages, modifier)`` tuple shape
* Routes through ``dispatch_full`` (covered: tool-level deny works)
* Per-tool persistence still engages
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from src.permissions.types import (
    PermissionDenyDecision,
    ToolPermissionContext,
)
from src.query.query import (
    _dispatch_single_tool,
    _find_assistant_message_for_block,
    _run_tools_partitioned,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult
from src.types.content_blocks import ToolUseBlock
from src.types.messages import AssistantMessage, UserMessage


def _make_context() -> ToolContext:
    tmp = tempfile.mkdtemp()
    ctx = ToolContext(
        workspace_root=Path(tmp),
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )
    return ctx


def _make_simple_tool(name: str = "Echo", output: Any = None,
                     new_messages: list[Any] | None = None,
                     context_modifier: Any = None,
                     concurrency_safe: bool = False):
    def _call(_inp, _ctx):
        return ToolResult(
            name=name,
            output=output if output is not None else {"ok": True},
            new_messages=new_messages,
            context_modifier=context_modifier,
        )
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=_call,
        is_concurrency_safe=lambda _i: concurrency_safe,
    )


class TestDispatchReturnShape(unittest.TestCase):
    """``_dispatch_single_tool`` now returns ``(messages, modifier)``."""

    def test_returns_tuple_of_messages_and_modifier(self) -> None:
        ctx = _make_context()
        tool = _make_simple_tool("Echo", output={"k": "v"})
        block = ToolUseBlock(id="b1", name="Echo", input={})
        fake_registry = MagicMock()

        result = _dispatch_single_tool(block, fake_registry, ctx, tools=[tool])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        messages, modifier = result
        self.assertIsInstance(messages, list)
        self.assertTrue(all(hasattr(m, "content") for m in messages))
        self.assertIsNone(modifier)

    def test_primary_result_first_in_list(self) -> None:
        ctx = _make_context()
        tool = _make_simple_tool("Echo", output="hi")
        block = ToolUseBlock(id="b-primary", name="Echo", input={})
        fake_registry = MagicMock()

        messages, _ = _dispatch_single_tool(block, fake_registry, ctx, tools=[tool])
        self.assertGreaterEqual(len(messages), 1)
        first = messages[0]
        self.assertIsInstance(first.content, list)
        # The primary message contains a ToolResultBlock with the call's id
        tool_use_id = getattr(first.content[0], "tool_use_id", None)
        self.assertEqual(tool_use_id, "b-primary")


class TestNewMessagesPropagation(unittest.TestCase):
    """``ToolResult.new_messages`` are appended after the primary result."""

    def test_new_messages_appear_in_returned_list(self) -> None:
        from src.types.messages import create_user_message

        extra = create_user_message(content=[{"type": "text", "text": "extra"}])
        ctx = _make_context()
        tool = _make_simple_tool("AgentLike", output={"transcript": "..."},
                                 new_messages=[extra])
        block = ToolUseBlock(id="b-agent", name="AgentLike", input={})
        fake_registry = MagicMock()

        messages, _ = _dispatch_single_tool(block, fake_registry, ctx, tools=[tool])
        # Primary + the one new_message
        self.assertEqual(len(messages), 2)


class TestContextModifierPropagation(unittest.TestCase):
    """``ToolResult.context_modifier`` is surfaced; caller applies it."""

    def test_context_modifier_surfaced(self) -> None:
        def _mod(c: ToolContext) -> ToolContext:
            c.plan_mode = True
            return c

        ctx = _make_context()
        tool = _make_simple_tool("PlanLike", output={"ok": True}, context_modifier=_mod)
        block = ToolUseBlock(id="b-plan", name="PlanLike", input={})
        fake_registry = MagicMock()

        _, modifier = _dispatch_single_tool(block, fake_registry, ctx, tools=[tool])
        self.assertIsNotNone(modifier)
        # Not yet applied
        self.assertFalse(ctx.plan_mode)
        modifier(ctx)
        self.assertTrue(ctx.plan_mode)


class TestPermissionDeny(unittest.TestCase):
    """Tool-level deny still propagates through dispatch_full."""

    def test_tool_deny_surfaces_as_error(self) -> None:
        def _deny(_inp, _ctx):
            return PermissionDenyDecision(behavior="deny", message="nope")

        ctx = _make_context()
        ctx.permission_context = ToolPermissionContext(mode="default")
        tool = build_tool(
            name="Blocked",
            input_schema={"type": "object", "properties": {}},
            call=lambda _i, _c: ToolResult(name="Blocked", output={}),
            check_permissions=_deny,
        )
        block = ToolUseBlock(id="b-deny", name="Blocked", input={})
        fake_registry = MagicMock()

        messages, _ = _dispatch_single_tool(block, fake_registry, ctx, tools=[tool])
        primary = messages[0].content[0]
        self.assertTrue(primary.is_error)


class TestSerialBatchContextModifier(unittest.TestCase):
    """Serial batches apply context_modifier IMMEDIATELY after each tool,
    so the next tool sees the mutated context.

    Concrete case from the chapter: ``[EnterPlanMode, Edit(/src/x.py)]``
    — EnterPlanMode's modifier must apply before Edit runs.
    """

    def test_serial_modifier_applied_between_tools(self) -> None:
        applied: list[bool] = []

        def _mod(c: ToolContext) -> ToolContext:
            c.plan_mode = True
            return c

        # Tool 1: sets plan_mode via context_modifier.
        plan_tool = _make_simple_tool("PlanLike", context_modifier=_mod)

        # Tool 2: reads ctx.plan_mode and records it (proves Tool 1's
        # modifier was applied BEFORE Tool 2 ran).
        def _capture(_inp, c):
            applied.append(c.plan_mode)
            return ToolResult(name="Capture", output={"saw_plan_mode": c.plan_mode})

        capture_tool = build_tool(
            name="Capture",
            input_schema={"type": "object", "properties": {}},
            call=_capture,
        )

        ctx = _make_context()
        blocks = [
            ToolUseBlock(id="t1", name="PlanLike", input={}),
            ToolUseBlock(id="t2", name="Capture", input={}),
        ]
        fake_registry = MagicMock()

        asyncio.run(_run_tools_partitioned(
            blocks, fake_registry, ctx, [plan_tool, capture_tool],
        ))

        self.assertTrue(applied, "Capture tool didn't run")
        self.assertTrue(applied[0], "Capture saw plan_mode=False; modifier wasn't "
                                    "applied between serial tools")


class TestConcurrentBatchContextModifier(unittest.TestCase):
    """Concurrent batches queue context_modifiers; apply after batch
    in submission order."""

    def test_concurrent_modifiers_applied_after_batch(self) -> None:
        order: list[str] = []

        def _mod_a(c: ToolContext) -> ToolContext:
            order.append("a")
            return c

        def _mod_b(c: ToolContext) -> ToolContext:
            order.append("b")
            return c

        tool_a = _make_simple_tool("A", context_modifier=_mod_a, concurrency_safe=True)
        tool_b = _make_simple_tool("B", context_modifier=_mod_b, concurrency_safe=True)

        ctx = _make_context()
        blocks = [
            ToolUseBlock(id="ta", name="A", input={}),
            ToolUseBlock(id="tb", name="B", input={}),
        ]
        fake_registry = MagicMock()

        asyncio.run(_run_tools_partitioned(
            blocks, fake_registry, ctx, [tool_a, tool_b],
        ))

        # Submission order ['a', 'b'] preserved.
        self.assertEqual(order, ["a", "b"])


class TestConcurrencyNotSerialized(unittest.TestCase):
    """The aggregate-budget lock must NOT serialize parallel I/O work.

    Critic raised: an earlier draft put the lock around the whole
    ``_dispatch_single_tool`` call which serialized parallel tool
    execution (5× regression on Read/Grep/Glob fan-out). The fix
    pushes the lock down to wrap only the budget read-decide-write
    inside ``run_tool_use``. This test verifies concurrent dispatches
    of an I/O-bound tool actually run in parallel.
    """

    def test_parallel_io_bound_tools_run_concurrently(self) -> None:
        import time
        from src.tool_system.build_tool import build_tool

        def _slow_call(_inp, _ctx):
            time.sleep(0.1)  # simulated I/O
            return ToolResult(name="SlowRead", output="x" * 100)

        slow_tool = build_tool(
            name="SlowRead",
            input_schema={"type": "object", "properties": {}},
            call=_slow_call,
            is_concurrency_safe=lambda _i: True,
            is_read_only=lambda _i: True,
        )

        ctx = _make_context()
        blocks = [
            ToolUseBlock(id=f"b{i}", name="SlowRead", input={})
            for i in range(6)
        ]
        fake_registry = MagicMock()

        t0 = time.monotonic()
        asyncio.run(_run_tools_partitioned(blocks, fake_registry, ctx, [slow_tool]))
        elapsed = time.monotonic() - t0

        # Serial would be 6 × 0.1 = 0.6s. Parallel should be ~0.1s.
        # Generous threshold of 0.4s to absorb scheduling overhead but
        # still catch a >2× regression.
        self.assertLess(
            elapsed, 0.4,
            f"Parallel batch took {elapsed:.2f}s; expected <0.4s if "
            f"concurrent execution is working. Lock may be serializing "
            f"too much.",
        )


class TestCloneStyleContextModifier(unittest.TestCase):
    """A context_modifier that returns a NEW (cloned) context must
    have its return value propagated to subsequent batches.

    Critic raised: the protocol allows modifier to return a new
    context; an earlier draft discarded the return value, breaking
    clone-style modifiers silently.
    """

    def test_clone_returning_modifier_propagates(self) -> None:
        import copy

        def _clone_mod(c: ToolContext) -> ToolContext:
            # Return a new ToolContext with plan_mode set
            new_ctx = copy.copy(c)
            new_ctx.plan_mode = True
            return new_ctx

        seen_states: list[bool] = []

        def _capture(_inp, c):
            seen_states.append(c.plan_mode)
            return ToolResult(name="Capture", output={})

        ctx = _make_context()
        tool_clone = _make_simple_tool("Cloner", context_modifier=_clone_mod)
        tool_capture = build_tool(
            name="Capture",
            input_schema={"type": "object", "properties": {}},
            call=_capture,
        )
        blocks = [
            ToolUseBlock(id="b1", name="Cloner", input={}),
            ToolUseBlock(id="b2", name="Capture", input={}),
        ]
        fake_registry = MagicMock()

        asyncio.run(_run_tools_partitioned(
            blocks, fake_registry, ctx, [tool_clone, tool_capture],
        ))

        self.assertTrue(seen_states, "Capture tool didn't run")
        self.assertTrue(
            seen_states[0],
            "Capture saw plan_mode=False; clone-style modifier "
            "didn't propagate to the next batch",
        )


class TestFindAssistantMessage(unittest.TestCase):
    """The helper that pairs ToolUseBlock with its emitting AssistantMessage."""

    def test_finds_message_by_block_id(self) -> None:
        block = ToolUseBlock(id="b1", name="Echo", input={})
        amsg = AssistantMessage(content=[block])
        result = _find_assistant_message_for_block(block, [amsg])
        self.assertIs(result, amsg)

    def test_returns_none_when_no_match(self) -> None:
        block = ToolUseBlock(id="b1", name="Echo", input={})
        amsg = AssistantMessage(content=[
            ToolUseBlock(id="other-id", name="Echo", input={}),
        ])
        result = _find_assistant_message_for_block(block, [amsg])
        self.assertIsNone(result)

    def test_returns_none_for_empty_list(self) -> None:
        block = ToolUseBlock(id="b1", name="Echo", input={})
        result = _find_assistant_message_for_block(block, [])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
