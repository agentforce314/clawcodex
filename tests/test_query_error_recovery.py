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

from src.query.query import (
    ESCALATED_MAX_TOKENS,
    QueryParams,
    StreamEvent,
    query,
)


def _run(coro):
    return asyncio.run(coro)


class TestMaxOutputTokensEscalation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_escalation_to_64k(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated = ChatResponse(
            content="Partial output...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output with more content.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5000},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated, full]

        messages = [UserMessage(content="Write a long story")]
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

        self.assertEqual(provider.chat.call_count, 2)

        second_call = provider.chat.call_args_list[1]
        self.assertEqual(second_call[1].get("max_tokens"), ESCALATED_MAX_TOKENS)

    def test_recovery_with_resume_message(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated_with_override = ChatResponse(
            content="Partial output again...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5000},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated_with_override, full]

        messages = [UserMessage(content="Write a long story")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
            max_output_tokens_override=ESCALATED_MAX_TOKENS,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        self.assertEqual(provider.chat.call_count, 2)


class TestRecoveryExhaustion(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recovery_stops_after_max_attempts(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated = ChatResponse(
            content="Partial...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        provider.chat.return_value = truncated

        messages = [UserMessage(content="Write a very long story")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=20,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        self.assertLessEqual(provider.chat.call_count, 6)


if __name__ == "__main__":
    unittest.main()
