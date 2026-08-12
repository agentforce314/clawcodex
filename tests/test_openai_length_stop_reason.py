"""OpenAI-compat ``finish_reason="length"`` must reach the recovery lanes.

Every stop_reason consumer in the query loop keys on the internal
(Anthropic) vocabulary — ``"max_tokens"`` — and none matched the OpenAI
wire's ``"length"``, so a truncated response from any OpenAI-compatible
provider (openai, openrouter, zai, deepseek, …) fell through as a normal
end-of-turn: no escalation, no "resume" recovery, and a headless run ended
with a silently-clipped answer. The second test also pins the tool-free
guard: a truncation that still carries a surviving tool call must be
yielded (so headless persistence sees the turn) rather than withheld.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.query.query import ESCALATED_MAX_TOKENS, QueryParams, query
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


class TestLengthFinishReasonNormalization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(self.temp_dir.name))
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _params(self, provider):
        return QueryParams(
            messages=[UserMessage(content="Write a long story")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

    def test_length_truncation_escalates_like_max_tokens(self):
        """OpenAI-wire truncation engages the same 64K escalation retry the
        Anthropic vocabulary always got."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        truncated = ChatResponse(
            content="Partial output...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="length",  # OpenAI vocabulary, not "max_tokens"
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 500},
            finish_reason="stop",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated, full]

        async def run():
            async for _ in query(self._params(provider)):
                pass

        asyncio.run(run())

        self.assertEqual(provider.chat.call_count, 2)
        second_call = provider.chat.call_args_list[1]
        self.assertEqual(second_call[1].get("max_tokens"), ESCALATED_MAX_TOKENS)

    def test_openai_stop_maps_to_end_turn_unchanged(self):
        """The normalization touches ONLY "length" — a normal "stop" turn
        must not grow a retry."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="stop",
                tool_uses=None,
            )
        ]

        async def run():
            async for _ in query(self._params(provider)):
                pass

        asyncio.run(run())
        self.assertEqual(provider.chat.call_count, 1)

    def test_truncated_turn_with_surviving_tool_call_is_yielded(self):
        """A length-truncated response that still carries a complete tool
        call must reach the yield stream (and thus on_message persistence).

        The max_output_tokens tag routes a message into the withholding
        gate, whose re-surfacing lanes live under ``not needs_follow_up`` —
        with tool calls present nothing re-surfaces, so tagging a
        tool-carrying truncation withheld the assistant turn while its
        tool_result still flowed: the saved session lost the turn and the
        orphaned result was dropped on resume. The tag must therefore be
        tool-free-only.
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        truncated_with_tool = ChatResponse(
            content="",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="length",
            tool_uses=[{
                "id": "t1",
                "name": "Bash",
                "input": {"command": "echo hi"},
            }],
        )
        done = ChatResponse(
            content="Finished.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated_with_tool, done]

        yielded = []

        async def run():
            async for msg in query(self._params(provider)):
                yielded.append(msg)

        asyncio.run(run())

        def _has_block(msg, btype):
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                return False
            return any(getattr(b, "type", None) == btype for b in content)

        tool_use_yielded = any(_has_block(m, "tool_use") for m in yielded)
        tool_result_yielded = any(
            isinstance(getattr(m, "content", None), list)
            and any(
                (isinstance(b, dict) and b.get("type") == "tool_result")
                or getattr(b, "type", None) == "tool_result"
                for b in m.content
            )
            for m in yielded
        )
        self.assertTrue(
            tool_use_yielded,
            "assistant turn carrying the tool_use must be yielded "
            f"(yielded types: {[type(m).__name__ for m in yielded]})",
        )
        self.assertTrue(tool_result_yielded)


if __name__ == "__main__":
    unittest.main()
