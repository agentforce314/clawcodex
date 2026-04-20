import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.engine import QueryEngine, QueryEngineConfig
from src.query.query import StreamEvent


def _run(coro):
    return asyncio.run(coro)


class TestQueryEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_engine(self, provider) -> QueryEngine:
        tools = self.registry.list_tools()
        config = QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=tools,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=10,
        )
        return QueryEngine(config)

    def test_submit_message_yields_assistant(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Test response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Hello"):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertEqual(len(assistants), 1)

    def test_messages_accumulate(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)

        async def run():
            async for _ in engine.submit_message("First"):
                pass

        _run(run())

        msgs = engine.get_messages()
        user_msgs = [m for m in msgs if isinstance(m, UserMessage)]
        assistant_msgs = [m for m in msgs if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(user_msgs), 1)
        self.assertGreaterEqual(len(assistant_msgs), 1)

    def test_interrupt(self):
        engine = self._make_engine(MagicMock())
        engine.interrupt()

    def test_reset_abort_controller(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)
        engine.interrupt()
        engine.reset_abort_controller()

        collected = []

        async def run():
            async for msg in engine.submit_message("Hello again"):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

    def test_session_id_exists(self):
        engine = self._make_engine(MagicMock())
        self.assertIsInstance(engine.session_id, str)
        self.assertGreater(len(engine.session_id), 0)


if __name__ == "__main__":
    unittest.main()
