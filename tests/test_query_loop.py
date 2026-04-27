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

from src.query.query import QueryParams, StreamEvent, query


def _run(coro):
    return asyncio.run(coro)


class TestQueryLoopSingleTurn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_single_turn_no_tools(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello, world!",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        messages = [UserMessage(content="Hi")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertEqual(len(assistants), 1)

        content = assistants[0].content
        if isinstance(content, list):
            text = "".join(b.text for b in content if isinstance(b, TextBlock))
        else:
            text = content
        self.assertIn("Hello", text)

    def test_multi_turn_with_tools(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        tool_use_response = ChatResponse(
            content="I'll create the file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "test.txt"),
                    "content": "hello",
                },
            }],
        )

        final_response = ChatResponse(
            content="File created!",
            model="test",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )

        provider.chat.side_effect = [tool_use_response, final_response]

        messages = [UserMessage(content="Create test.txt")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

        tool_results = [
            m for m in collected
            if isinstance(m, UserMessage) and isinstance(m.content, list)
            and any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        self.assertGreaterEqual(len(tool_results), 1)

    def test_multi_turn_replays_reasoning_content_for_followup(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        first = ChatResponse(
            content="I'll handle this",
            model="deepseek-v4-pro",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            reasoning_content="thinking trace from provider",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "reasoning.txt"),
                    "content": "hello",
                },
            }],
        )
        second = ChatResponse(
            content="Done",
            model="deepseek-v4-pro",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [first, second]

        params = QueryParams(
            messages=[UserMessage(content="Create file")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        async def run():
            async for _msg in query(params):
                pass

        _run(run())

        self.assertEqual(provider.chat.call_count, 2)
        second_call_messages = provider.chat.call_args_list[1].args[0]
        assistant_with_tool_use = next(
            msg for msg in second_call_messages
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list)
        )
        self.assertEqual(
            assistant_with_tool_use.get("reasoning_content"),
            "thinking trace from provider",
        )

    def test_max_turns_limit(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        tool_use_response = ChatResponse(
            content="Working...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "test.txt"),
                    "content": "hello",
                },
            }],
        )

        provider.chat.return_value = tool_use_response

        messages = [UserMessage(content="Create test.txt")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=2,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        max_turns_msgs = [
            m for m in collected
            if isinstance(m, SystemMessage) and getattr(m, "subtype", None) == "max_turns_reached"
        ]
        self.assertEqual(len(max_turns_msgs), 1)


class TestQueryLoopAbort(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_abort_before_response(self):
        abort = AbortController()
        abort.abort("test_abort")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Should not see this",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        messages = [UserMessage(content="Hi")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        interruptions = [
            m for m in collected
            if isinstance(m, UserMessage) and m.isMeta
        ]
        self.assertGreaterEqual(len(interruptions), 1)


if __name__ == "__main__":
    unittest.main()
