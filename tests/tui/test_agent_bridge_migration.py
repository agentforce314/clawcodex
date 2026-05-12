"""Ch5/F.3 acceptance tests: TUI AgentBridge always routes through
the canonical query() loop.

The TUI worker dispatch is exercised indirectly: we call
_run_agent_in_thread directly (after wiring a minimal AgentBridge
with a fake session, provider, etc.) and assert that the canonical
loop's adapter was invoked.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry


class _FakeSession:
    """Minimal stand-in for src.agent.Session."""
    def __init__(self):
        from src.agent.conversation import Conversation
        self.conversation = Conversation()


class _FakeState:
    """Minimal stand-in for src.tui.state.AppState."""
    def set_thinking(self, *_a, **_k): pass
    def mark_tool_started(self, *_a, **_k): pass
    def mark_tool_finished(self, *_a, **_k): pass
    def append_streaming_text(self, *_a, **_k): pass
    def clear_streaming_text(self, *_a, **_k): pass


def _make_bridge(workspace: Path, provider, run_worker=None):
    """Construct an AgentBridge with all dependencies fake."""
    from src.tui.agent_bridge import AgentBridge
    bridge = AgentBridge(
        post_message=lambda m: None,
        session=_FakeSession(),
        provider=provider,
        tool_registry=build_default_registry(),
        tool_context=ToolContext(workspace_root=workspace),
        app_state=_FakeState(),
        run_worker=run_worker or (lambda *a, **k: None),
        max_turns=3,
        stream=False,
    )
    return bridge


class TestTUIRoutesThroughCanonicalLoop(unittest.TestCase):
    """The TUI bridge always invokes run_query_as_agent_loop —
    no opt-in flag. Verified by spying on the adapter import."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.provider = MagicMock()
        self.provider.chat_stream_response.side_effect = NotImplementedError()
        self.provider.chat.return_value = ChatResponse(
            content="ok",
            model="t",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _drive(self, bridge):
        bridge._session.conversation.add_user_message("hi")
        with bridge._busy_lock:
            bridge._busy = True
            from src.utils.abort_controller import AbortController
            bridge._abort_controller = AbortController()
        try:
            bridge._run_agent_in_thread()
        finally:
            with bridge._busy_lock:
                bridge._busy = False

    def test_bridge_invokes_canonical_adapter(self):
        bridge = _make_bridge(self.workspace, self.provider)

        adapter_calls = {"n": 0}

        async def fake_adapter(**kwargs):
            adapter_calls["n"] += 1
            from src.query.agent_loop_compat import AgentLoopRunResult
            from src.query.transitions import Terminal
            return AgentLoopRunResult(
                response_text="from adapter",
                usage={"input_tokens": 1, "output_tokens": 1},
                num_turns=1,
                terminal=Terminal(reason="completed"),
            )

        # Patch the binding inside the importing module
        # (`src.tui.agent_bridge`) — survives any future refactor
        # that reorganizes the import block.
        with patch(
            "src.tui.agent_bridge.run_query_as_agent_loop",
            new=fake_adapter,
        ):
            self._drive(bridge)

        self.assertEqual(adapter_calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
