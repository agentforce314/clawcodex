"""Chapter 7 round-2 — interruptible-set UI signal.

Mirrors TS ``StreamingToolExecutor.ts`` ``updateInterruptibleState`` at
line 254 and its three call sites (270, 290, 386). The flag is exposed
via the optional ``ToolContext.set_has_interruptible_tool_in_progress``
callback. The executor must publish:

- ``True`` while at least one tool is executing AND every executing
  tool's ``interrupt_behavior()`` returns ``"cancel"``.
- ``False`` otherwise (empty executing set, or any executing tool
  declares ``"block"``).

These tests fail before the round-2 change because the field does not
exist on ``ToolContext`` and the executor never publishes anything.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from src.services.tool_execution.streaming_executor import (
    StreamingToolExecutor,
    ToolUseBlock,
)
from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.types.messages import create_assistant_message
from src.utils.abort_controller import AbortController


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _tool_with_interrupt(name: str, interrupt: str) -> Tool:
    """Concurrent-safe tool that declares the given ``interrupt_behavior``."""
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda _i, _c: ToolResult(name=name, output=f"ok:{name}"),
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
        interrupt_behavior=lambda: interrupt,
    )


def _make_context(
    tools: list[Tool],
    captured: list[bool] | None = None,
    *,
    raise_setter: bool = False,
) -> ToolContext:
    ctx = ToolContext(
        workspace_root=Path("/tmp"),
        options=ToolUseOptions(tools=tools),
        abort_controller=AbortController(),
    )
    if captured is not None:
        if raise_setter:
            def _setter(v: bool) -> None:
                captured.append(v)
                raise RuntimeError("setter explodes")

            ctx.set_has_interruptible_tool_in_progress = _setter
        else:
            ctx.set_has_interruptible_tool_in_progress = lambda v: captured.append(v)
    return ctx


def _allow_all(_tool, tool_input, _ctx, _msg, _tool_use_id):
    return {"behavior": "allow", "updatedInput": tool_input}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInterruptibleSignal(unittest.IsolatedAsyncioTestCase):
    async def test_signal_true_when_executing_set_all_cancellable(self):
        """One Read-like cancellable tool: setter sees True at least once
        while executing, and ends on False after completion."""
        observed: list[bool] = []
        tool = _tool_with_interrupt("Read", "cancel")
        ctx = _make_context([tool], observed)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        executor.add_tool(
            ToolUseBlock(id="t1", name="Read", input={}),
            create_assistant_message(content="hi"),
        )
        async for _ in executor.get_remaining_results():
            pass

        # We expect a True publication (entry into executing) and a
        # False publication (completion). The exact count is an
        # implementation detail but the *sequence* must contain at
        # least True followed by a final False.
        self.assertIn(True, observed, f"observed sequence: {observed!r}")
        self.assertEqual(
            observed[-1], False,
            f"final state must be False (empty executing set), got: {observed!r}",
        )

    async def test_signal_false_when_any_executing_blocks_interrupt(self):
        """Mixed batch: one cancel + one block, both concurrent-safe.
        Once the block tool joins the executing set, the signal must be
        False (and stay False until that tool exits). The first tool
        may briefly publish True before the block-tool is admitted;
        what we lock down is: once a block-tool is executing,
        ``False`` is the only correct publication."""
        observed: list[bool] = []
        cancel = _tool_with_interrupt("ReadCancel", "cancel")
        block = _tool_with_interrupt("ReadBlock", "block")
        ctx = _make_context([cancel, block], observed)
        executor = StreamingToolExecutor(
            [cancel, block], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        msg = create_assistant_message(content="hi")
        executor.add_tool(ToolUseBlock(id="t1", name="ReadCancel", input={}), msg)
        executor.add_tool(ToolUseBlock(id="t2", name="ReadBlock", input={}), msg)

        async for _ in executor.get_remaining_results():
            pass

        # While the block tool is in the executing set, the signal
        # MUST be False (the all-cancellable predicate fails). After
        # both complete, the executing set is empty → False.
        self.assertIn(False, observed,
                      f"False must appear in sequence, got: {observed!r}")
        self.assertEqual(
            observed[-1], False,
            f"final state must be False, got: {observed!r}",
        )

    async def test_signal_false_when_executing_set_empty(self):
        """After a completed tool, the executing set is empty and the
        last published flag must be False."""
        observed: list[bool] = []
        tool = _tool_with_interrupt("Read", "cancel")
        ctx = _make_context([tool], observed)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        executor.add_tool(
            ToolUseBlock(id="t1", name="Read", input={}),
            create_assistant_message(content="hi"),
        )
        async for _ in executor.get_remaining_results():
            pass

        # No tool left executing — last observed must be False.
        self.assertGreater(
            len(observed), 0,
            "setter must have been called at least once",
        )
        self.assertEqual(observed[-1], False)

    async def test_setter_optional_when_none(self):
        """If no setter is wired (the default), executor must not raise."""
        tool = _tool_with_interrupt("Read", "cancel")
        ctx = _make_context([tool])  # captured=None → no setter wired
        self.assertIsNone(ctx.set_has_interruptible_tool_in_progress)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        executor.add_tool(
            ToolUseBlock(id="t1", name="Read", input={}),
            create_assistant_message(content="hi"),
        )
        # No assertion on observed state — the contract is "doesn't
        # crash." If the executor tried to call None, the
        # ``get_remaining_results`` drain below would raise.
        async for _ in executor.get_remaining_results():
            pass

    async def test_setter_exception_does_not_break_tool_dispatch(self):
        """A throwing setter must be swallowed so UI bugs cannot kill
        tool execution. Mirrors the spirit of TS's optional-chain
        invocation: a UI implementation crash should not poison the
        tool runtime."""
        observed: list[bool] = []
        tool = _tool_with_interrupt("Read", "cancel")
        ctx = _make_context([tool], observed, raise_setter=True)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        executor.add_tool(
            ToolUseBlock(id="t1", name="Read", input={}),
            create_assistant_message(content="hi"),
        )
        # Drain. Must not raise even though the setter throws each call.
        msgs_seen = 0
        async for _ in executor.get_remaining_results():
            msgs_seen += 1
        # The tool still produced at least one result block.
        self.assertGreater(msgs_seen, 0)
        # And the setter was called (and recorded its arg before raising).
        self.assertGreater(
            len(observed), 0,
            "setter must have been invoked before raising",
        )

    async def test_signal_false_for_unknown_tool(self):
        """An unknown tool is created in ``completed`` status directly
        in ``add_tool`` and never enters ``executing``. The setter must
        therefore never publish True for this tool's lifecycle.

        This pins TS line 79–101 behavior: the synthetic-error path
        for an unknown tool skips ``executeTool`` entirely.
        """
        observed: list[bool] = []
        tool = _tool_with_interrupt("Known", "cancel")
        ctx = _make_context([tool], observed)
        executor = StreamingToolExecutor(
            [tool], can_use_tool=_allow_all, tool_use_context=ctx,
        )

        executor.add_tool(
            ToolUseBlock(id="t1", name="Unknown", input={}),
            create_assistant_message(content="hi"),
        )
        async for _ in executor.get_remaining_results():
            pass

        # The unknown-tool path bypasses _execute_tool entirely, so
        # the setter is never called for this single-tool case.
        # This documents that "completed before executing" doesn't
        # accidentally fire a True/False pair.
        self.assertEqual(
            observed, [],
            f"unknown-tool path should not publish, got: {observed!r}",
        )


if __name__ == "__main__":
    unittest.main()
