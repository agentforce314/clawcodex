"""Ch5/F.1 acceptance tests: run_query_as_agent_loop adapter."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.query.agent_loop_compat import (
    AgentLoopRunResult,
    ToolEvent,
    run_query_as_agent_loop,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry


def _run(coro):
    return asyncio.run(coro)


class TestAdapterShape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_agent_loop_run_result_with_terminal(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello.",
            model="t",
            usage={"input_tokens": 3, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

        convo = Conversation()
        convo.add_user_message("Say hi")

        result = _run(run_query_as_agent_loop(
            conversation=convo,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            max_turns=5,
        ))

        self.assertIsInstance(result, AgentLoopRunResult)
        self.assertEqual(result.response_text, "Hello.")
        self.assertEqual(result.terminal.reason, "completed")
        self.assertGreaterEqual(result.num_turns, 1)
        self.assertGreater(result.usage["input_tokens"], 0)


class TestAdapterTextChunkCallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_on_text_chunk_receives_final_text(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Result.",
            model="t",
            usage={"input_tokens": 3, "output_tokens": 2},
            finish_reason="end_turn",
            tool_uses=None,
        )

        convo = Conversation()
        convo.add_user_message("Go")

        chunks: list[str] = []
        _run(run_query_as_agent_loop(
            conversation=convo,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            on_text_chunk=chunks.append,
            stream=True,
        ))
        self.assertEqual("".join(chunks), "Result.")


class TestAdapterRealTimeEventDispatch(unittest.TestCase):
    """Per critic: adapter must dispatch on_event / on_text_chunk
    INCREMENTALLY as messages arrive, not in a single burst at the
    end. Verifies UX parity with the legacy run_agent_loop dispatch.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_chunks_arrive_per_assistant_message(self):
        """Two assistant turns → on_text_chunk fires twice, in order.

        Verifies INCREMENTAL delivery (not just final ordering):
        when the second chunk arrives, the tool_result event must
        already have been seen. A regression that buffered until
        the end would deliver both chunks BEFORE either tool event,
        and this test would fail.
        """
        from src.types.messages import UserMessage

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        hello_path = self.workspace / "hello.py"
        provider.chat.side_effect = [
            ChatResponse(
                content="Working on it...",
                model="t",
                usage={"input_tokens": 5, "output_tokens": 4},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "toolu_1",
                    "name": "Write",
                    "input": {
                        "file_path": str(hello_path),
                        "content": "print('hi')",
                    },
                }],
            ),
            ChatResponse(
                content="Done.",
                model="t",
                usage={"input_tokens": 5, "output_tokens": 1},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        convo = Conversation()
        convo.add_user_message("Create hello.py")

        trace: list[tuple] = []

        def on_chunk(text: str) -> None:
            trace.append(("chunk", text))

        def on_event(ev: ToolEvent) -> None:
            trace.append(("event", ev.kind, ev.tool_name))

        _run(run_query_as_agent_loop(
            conversation=convo,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            on_event=on_event,
            on_text_chunk=on_chunk,
            stream=True,
        ))

        def _idx(predicate):
            for i, t in enumerate(trace):
                if predicate(t):
                    return i
            return -1

        first_chunk_idx = _idx(lambda t: t == ("chunk", "Working on it..."))
        tool_use_idx = _idx(lambda t: t == ("event", "tool_use", "Write"))
        tool_result_idx = _idx(lambda t: t == ("event", "tool_result", "Write"))
        second_chunk_idx = _idx(lambda t: t == ("chunk", "Done."))

        self.assertGreaterEqual(first_chunk_idx, 0)
        self.assertGreaterEqual(tool_use_idx, 0)
        self.assertGreaterEqual(tool_result_idx, 0)
        self.assertGreaterEqual(second_chunk_idx, 0)

        # The INCREMENTAL contract: each marker happens before the
        # next one in stream order.
        self.assertLess(first_chunk_idx, tool_use_idx)
        self.assertLess(tool_use_idx, tool_result_idx)
        self.assertLess(tool_result_idx, second_chunk_idx)


class TestAdapterPTLRecovery(unittest.TestCase):
    """Phase F: the adapter MUST surface the recovery infrastructure
    that the legacy run_agent_loop lacked. PTL recovery should work
    end-to-end via the canonical query() loop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ptl_surfaces_terminal_via_adapter(self):
        from unittest.mock import patch
        from src.services.compact.reactive_compact import ReactiveCompactResult
        from src.types.messages import UserMessage

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = RuntimeError("Prompt is too long.")

        async def fail_rc(**kwargs):
            return ReactiveCompactResult(
                compacted=False,
                messages=kwargs["messages"],
                tokens_before=1000,
                error="mocked",
            )

        convo = Conversation()
        convo.add_user_message("Long task")

        with patch(
            "src.services.compact.reactive_compact.reactive_compact",
            side_effect=fail_rc,
        ):
            result = _run(run_query_as_agent_loop(
                conversation=convo,
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
            ))

        self.assertEqual(result.terminal.reason, "prompt_too_long")


if __name__ == "__main__":
    unittest.main()
