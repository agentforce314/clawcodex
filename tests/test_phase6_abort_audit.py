"""Phase 6 audit: verify abort propagation through ``dispatch_full``.

The Phase 6 plan deliberately scopes this to documentation +
verification. The actual abort-boundary check happens inside
``run_tool_use:99-105`` (set via ``tool_context.abort_controller``).
After Phases 3+4, every dispatch goes through that path, so the
behavior is inherited for free.

This test confirms the propagation by triggering abort_controller
BEFORE calling ``dispatch_full`` and asserting the call returns a
cancel result without running the tool.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from src.permissions.types import ToolPermissionContext
from src.services.tool_execution.sync_adapter import (
    dispatch_full,
    make_stub_assistant_message,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall, ToolResult
from src.utils.abort_controller import AbortController


def _make_context_with_abort() -> tuple[ToolContext, AbortController]:
    tmp = tempfile.mkdtemp()
    abort = AbortController()
    ctx = ToolContext(
        workspace_root=Path(tmp),
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
        abort_controller=abort,
    )
    return ctx, abort


class TestAbortBoundaryCheck(unittest.TestCase):
    """``run_tool_use`` checks ``abort_controller.signal.aborted`` at
    the top of every tool call. After Phase 3+4 routing, this check
    applies to every production dispatch automatically."""

    def test_aborted_signal_short_circuits_dispatch(self) -> None:
        call_count = 0

        def _call(_inp, _ctx):
            nonlocal call_count
            call_count += 1
            return ToolResult(name="Echo", output={"ran": True})

        tool = build_tool(
            name="Echo",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )

        ctx, abort = _make_context_with_abort()
        # Trip the abort signal BEFORE dispatching.
        abort.abort("user_interrupt")

        call = ToolCall(name="Echo", input={}, tool_use_id="tu-abort")
        result = dispatch_full(
            call, ctx, make_stub_assistant_message(), tools=[tool],
        )

        # Tool should NOT have executed.
        self.assertEqual(call_count, 0,
                         "Tool ran despite abort signal — boundary check failed")
        # Result should be flagged as an error (cancellation surfaces
        # via tool_result with is_error=True and a cancel message).
        self.assertTrue(result.is_error)

    def test_unset_abort_signal_does_not_block(self) -> None:
        """Sanity check: when abort is NOT set, dispatch runs normally."""
        call_count = 0

        def _call(_inp, _ctx):
            nonlocal call_count
            call_count += 1
            return ToolResult(name="Echo", output={"ran": True})

        tool = build_tool(
            name="Echo",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )

        ctx, _abort = _make_context_with_abort()
        # Don't set abort.

        call = ToolCall(name="Echo", input={}, tool_use_id="tu-noabort")
        result = dispatch_full(
            call, ctx, make_stub_assistant_message(), tools=[tool],
        )

        self.assertEqual(call_count, 1)
        self.assertFalse(result.is_error)


if __name__ == "__main__":
    unittest.main()
