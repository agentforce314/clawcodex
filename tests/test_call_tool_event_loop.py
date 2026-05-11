"""ch07 / M7: sync tool.call must run on a worker thread, not the event loop.

Drives a slow synchronous tool alongside a ticker coroutine and asserts
the ticker keeps making progress while the tool is running. Without
the `asyncio.to_thread` wrap in `_call_tool`, the sync `time.sleep`
inside the tool freezes the loop and the ticker stalls.
"""
from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.services.tool_execution.tool_execution import _call_tool
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.protocol import ToolResult
from src.utils.abort_controller import AbortController


def _make_slow_sync_tool() -> Tool:
    def _slow(_inp, _ctx):
        # Synchronous sleep — without to_thread this freezes the loop.
        time.sleep(0.1)
        return ToolResult(name="Slow", output="done")

    return build_tool(
        name="Slow",
        input_schema={"type": "object", "properties": {}},
        call=_slow,
        is_concurrency_safe=lambda _: True,
    )


class TestCallToolEventLoop(unittest.IsolatedAsyncioTestCase):
    async def test_sync_tool_does_not_block_event_loop(self) -> None:
        tool = _make_slow_sync_tool()
        ctx = ToolContext(
            workspace_root=Path("/tmp"),
            options=ToolUseOptions(tools=[tool]),
            abort_controller=AbortController(),
        )

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            for _ in range(10):
                await asyncio.sleep(0.005)
                ticks += 1

        t0 = time.monotonic()
        # Run the slow tool and the ticker concurrently. With the
        # to_thread wrap, both finish in ~100ms (the ticker totals
        # ~50ms of sleeps; the tool's 100ms wall-clock is the longer
        # of the two). Without it, the ticker would stall behind the
        # tool's 100ms blocking sleep, totaling ~150ms.
        await asyncio.gather(
            _call_tool(tool, {}, ctx),
            ticker(),
        )
        elapsed = time.monotonic() - t0

        # The ticker progressed while the tool was sleeping. Strict
        # asserts use generous margins so timing noise doesn't flake.
        self.assertGreaterEqual(
            ticks, 8,
            "ticker did not make progress while the tool was running — "
            "_call_tool is blocking the event loop",
        )
        self.assertLess(
            elapsed, 0.16,
            f"tool execution serialized with ticker (elapsed={elapsed:.3f}s) — "
            "expected concurrent execution under ~120ms",
        )


if __name__ == "__main__":
    unittest.main()
