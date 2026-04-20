import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.engine import QueryEngine, QueryEngineConfig
from src.query.query import StreamEvent


def _run(coro):
    return asyncio.run(coro)


class TestQueryIntegrationToolCalls(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_engine(self, provider, max_turns=10) -> QueryEngine:
        tools = self.registry.list_tools()
        config = QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=tools,
            tool_context=self.context,
            system_prompt="You are helpful.",
            max_turns=max_turns,
        )
        return QueryEngine(config)

    def test_full_write_tool_roundtrip(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        file_path = str(self.workspace / "output.txt")

        tool_use_response = ChatResponse(
            content="Creating file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_int_001",
                "name": "Write",
                "input": {"file_path": file_path, "content": "integration test content"},
            }],
        )
        final_response = ChatResponse(
            content="File created successfully.",
            model="test",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [tool_use_response, final_response]

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Create output.txt"):
                collected.append(msg)

        _run(run())

        self.assertTrue(Path(file_path).exists())
        self.assertEqual(Path(file_path).read_text(), "integration test content")

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

        tool_results = [
            m for m in collected
            if isinstance(m, UserMessage) and isinstance(m.content, list)
        ]
        self.assertGreaterEqual(len(tool_results), 1)

    def test_read_after_write_roundtrip(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        file_path = str(self.workspace / "data.txt")
        Path(file_path).write_text("pre-existing content\nline 2\n")

        tool_use_response = ChatResponse(
            content="Reading file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_int_002",
                "name": "Read",
                "input": {"file_path": file_path},
            }],
        )
        final_response = ChatResponse(
            content="The file contains pre-existing content.",
            model="test",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [tool_use_response, final_response]

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Read data.txt"):
                collected.append(msg)

        _run(run())

        tool_results = [
            m for m in collected
            if isinstance(m, UserMessage) and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        self.assertGreaterEqual(len(tool_results), 1)

        result_block = None
        for msg in tool_results:
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    result_block = block
                    break
        self.assertIsNotNone(result_block)
        self.assertFalse(result_block.is_error)

    def test_multi_tool_sequential(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        file1 = str(self.workspace / "a.txt")
        file2 = str(self.workspace / "b.txt")

        first_response = ChatResponse(
            content="Creating first file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_multi_1",
                "name": "Write",
                "input": {"file_path": file1, "content": "file a"},
            }],
        )
        second_response = ChatResponse(
            content="Creating second file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_multi_2",
                "name": "Write",
                "input": {"file_path": file2, "content": "file b"},
            }],
        )
        final_response = ChatResponse(
            content="Both files created.",
            model="test",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [first_response, second_response, final_response]

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Create a.txt and b.txt"):
                collected.append(msg)

        _run(run())

        self.assertTrue(Path(file1).exists())
        self.assertTrue(Path(file2).exists())
        self.assertEqual(Path(file1).read_text(), "file a")
        self.assertEqual(Path(file2).read_text(), "file b")

    def test_engine_state_persists_across_calls(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Ok",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        engine = self._make_engine(provider)

        async def run():
            async for _ in engine.submit_message("First"):
                pass
            async for _ in engine.submit_message("Second"):
                pass

        _run(run())

        msgs = engine.get_messages()
        user_msgs = [m for m in msgs if isinstance(m, UserMessage)]
        self.assertGreaterEqual(len(user_msgs), 2)


class TestQueryIntegrationErrors(unittest.TestCase):
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

    def test_api_error_handled_gracefully(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = Exception("API connection error")

        engine = self._make_engine(provider)
        collected = []

        async def run():
            async for msg in engine.submit_message("Hello"):
                collected.append(msg)

        _run(run())

        error_msgs = [
            m for m in collected
            if isinstance(m, AssistantMessage)
            and isinstance(m.content, str)
            and "error" in m.content.lower()
        ]
        self.assertGreaterEqual(len(error_msgs), 1)


if __name__ == "__main__":
    unittest.main()
