"""Ch5/F.1 — adapter tests for run_query_as_agent_loop.

Verifies that the canonical query() loop can be driven through an
AgentLoopResult-shaped interface so the headless and TUI production
paths can migrate off the legacy run_agent_loop.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController

from src.query.agent_loop_compat import (
    AgentLoopRunResult,
    run_query_as_agent_loop,
)


def _run(coro):
    return asyncio.run(coro)


class TestAgentLoopCompatAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adapter_returns_agent_loop_run_result_shape(self):
        """F.1: the adapter returns an AgentLoopRunResult with the
        same fields as the legacy AgentLoopResult, plus a Terminal."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello from query()",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="Hi")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=5,
        ))

        self.assertIsInstance(result, AgentLoopRunResult)
        self.assertEqual(result.response_text, "Hello from query()")
        self.assertEqual(result.usage["input_tokens"], 10)
        self.assertEqual(result.usage["output_tokens"], 5)
        self.assertGreaterEqual(result.num_turns, 1)
        self.assertIsNotNone(result.terminal)
        self.assertEqual(result.terminal.reason, "completed")

    def test_adapter_propagates_terminal_max_turns(self):
        """F.1: when max_turns is reached, the adapter surfaces
        Terminal(reason='max_turns')."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Always returns a tool_use so the loop never exits cleanly.
        provider.chat.return_value = ChatResponse(
            content="thinking",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "tool_1",
                "name": "Bash",
                "input": {"command": "true", "description": "noop"},
            }],
        )

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="Hi")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=2,
        ))

        self.assertIsNotNone(result.terminal)
        self.assertEqual(result.terminal.reason, "max_turns")

    def test_adapter_dispatches_on_event(self):
        """F.1: on_event receives tool_use and tool_result events."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Let me run a command",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "tool_1",
                    "name": "Bash",
                    "input": {"command": "true", "description": "noop"},
                }],
            ),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 50, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        events = []

        def collector(event):
            events.append(event)

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="Run something")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=5,
            on_event=collector,
        ))

        # Should have at least one tool_use event and at least one
        # tool_result event.
        kinds = [e.kind for e in events]
        self.assertIn("tool_use", kinds)
        self.assertIn("tool_result", kinds)

    def test_adapter_handles_cancel_signal(self):
        """F.1: a pre-set cancel_signal causes the loop to exit
        immediately via aborted_streaming/aborted_tools."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="should not reach",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        cancel = AbortController()
        cancel.abort("user_interrupt")

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="Hi")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=5,
            cancel_signal=cancel.signal,
        ))

        self.assertIsNotNone(result.terminal)
        self.assertIn(result.terminal.reason, ("aborted_streaming", "aborted_tools"))


if __name__ == "__main__":
    unittest.main()
