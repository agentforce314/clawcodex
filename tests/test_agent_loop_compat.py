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
        """Critic C1: a pre-set cancel_signal causes the adapter to raise
        AbortError so callers' existing ``except AbortError`` paths fire
        (headless emits ``cancelled`` ResultEvent + exit 130; TUI posts
        ``AgentRunFinished(error="Cancelled by user")``). Without this
        the cutover regressed headless to exit 0 with no signal."""
        from src.utils.abort_controller import AbortError
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

        with self.assertRaises(AbortError):
            _run(run_query_as_agent_loop(
                initial_messages=[UserMessage(content="Hi")],
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                system_prompt="You are helpful.",
                max_turns=5,
                cancel_signal=cancel.signal,
            ))


class TestLiveStreamingAndPersistence(unittest.TestCase):
    """Critic-flagged BLOCKING fixes — live streaming + full-message
    persistence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_on_text_chunk_forwarded_to_provider_for_live_streaming(self):
        """Critic BLOCKING #1: on_text_chunk must reach the provider's
        chat_stream_response so chunks fire LIVE during the model
        stream — not once at the end."""
        provider = MagicMock()
        provider.chat.return_value = ChatResponse(
            content="full text",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        # Make chat_stream_response a real callable that fires
        # on_text_chunk per simulated chunk and returns a ChatResponse.
        def fake_stream(api_messages, **kwargs):
            cb = kwargs.get("on_text_chunk")
            if cb is not None:
                for chunk in ["hel", "lo, ", "world"]:
                    cb(chunk)
            return ChatResponse(
                content="hello, world",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            )
        provider.chat_stream_response.side_effect = fake_stream

        chunks: list[str] = []

        def collector(text: str) -> None:
            chunks.append(text)

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="say hi")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=2,
            on_text_chunk=collector,
        ))

        self.assertEqual(result.terminal.reason, "completed")
        # Three live chunks fired by the provider — proving the
        # callback was threaded all the way through, NOT a single
        # post-hoc fire of the full text.
        self.assertEqual(chunks, ["hel", "lo, ", "world"])

    def test_on_message_persists_full_message_objects(self):
        """Critic BLOCKING #2: on_message must fire for every yielded
        Message so callers can persist the full structure
        (tool_use blocks intact for Anthropic multi-turn). Not
        response_text alone."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Turn 1: tool_use. Turn 2: text-only completion.
        provider.chat.side_effect = [
            ChatResponse(
                content="thinking",
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

        persisted: list = []

        def keep_full(msg) -> None:
            persisted.append(msg)

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="run noop")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=5,
            on_message=keep_full,
        ))

        self.assertEqual(result.terminal.reason, "completed")
        # We expect at least: assistant turn 1 (with tool_use),
        # user (with tool_result), assistant turn 2 (text).
        from src.types.messages import AssistantMessage as _AM, UserMessage as _UM
        from src.types.content_blocks import ToolUseBlock, ToolResultBlock
        assistants = [m for m in persisted if isinstance(m, _AM)]
        self.assertGreaterEqual(len(assistants), 2)
        # First assistant must carry a ToolUseBlock — proving full
        # structure was preserved (not just text).
        first_blocks = assistants[0].content
        self.assertTrue(
            isinstance(first_blocks, list)
            and any(isinstance(b, ToolUseBlock) for b in first_blocks),
            "Full ToolUseBlock structure must be persisted, not "
            "just text. Got: %r" % first_blocks,
        )
        # A user-message with tool_result must also be persisted
        # so the next API call can pair tool_use IDs.
        users_with_tool_result = [
            m for m in persisted
            if isinstance(m, _UM)
            and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        self.assertGreaterEqual(len(users_with_tool_result), 1)


if __name__ == "__main__":
    unittest.main()
