"""Concurrent workflow agents must run their model calls in PARALLEL.

Regression: the sync provider call ran on the asyncio event loop, so a workflow's
parallel() fan-out serialized — each agent's model call blocked the loop until it
finished (observed live: search agents stuck at "0 tokens"). The provider call now
runs off-loop (to_thread) when there's no live-UI callback.
"""

from __future__ import annotations

import asyncio
import time

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT
from src.utils.abort_controller import create_abort_controller
from src.workflow.runner import LiveAgentRunner
from src.workflow.types import AgentSpec

_SLEEP = 0.4


class _SleepProvider:
    """Each model call blocks for _SLEEP seconds (simulates real latency)."""

    model = "fake"

    def _resp(self):
        return ChatResponse(
            content="ok", model="fake",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="stop", tool_uses=None,
        )

    def chat_stream_response(self, messages, tools=None, on_text_chunk=None, abort_signal=None, **kw):
        time.sleep(_SLEEP)
        return self._resp()

    def chat(self, messages, tools=None, **kw):
        time.sleep(_SLEEP)
        return self._resp()


def _runner(provider, tmp_path):
    registry = build_default_registry(provider=provider)
    return LiveAgentRunner(
        provider=provider, tool_registry=registry,
        parent_context=ToolContext(workspace_root=tmp_path),
        base_tools=list(registry.list_tools()),
        resolve_agent=lambda _t: GENERAL_PURPOSE_AGENT, run_id="conc", max_turns=2,
    )


async def test_concurrent_agents_run_in_parallel(tmp_path):
    runner = _runner(_SleepProvider(), tmp_path)

    async def one(i):
        return await runner.run(
            AgentSpec(prompt=f"reply {i}"), abort=create_abort_controller(), index=str(i),
        )

    t0 = time.monotonic()
    outs = await asyncio.gather(*[one(i) for i in range(4)])
    dt = time.monotonic() - t0

    assert all(o is not None for o in outs)
    # 4 agents x 0.4s: serialized -> ~1.6s; parallel -> ~0.4s. Generous bound.
    assert dt < 1.0, f"agents serialized ({dt:.2f}s) — off-loop model call regressed"
