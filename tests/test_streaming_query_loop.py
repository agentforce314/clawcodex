from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.query.streaming import (
    QueryEvent,
    QueryTurn,
    StreamingQueryState,
    streaming_query,
)
from src.query.config import QueryConfig
from src.services.api.logging import NonNullableUsage


class TestQueryEvent(unittest.TestCase):
    def test_basic_event(self) -> None:
        event = QueryEvent(type="text", data={"text": "hello"})
        self.assertEqual(event.type, "text")
        self.assertEqual(event.data["text"], "hello")

    def test_event_no_data(self) -> None:
        event = QueryEvent(type="aborted")
        self.assertIsNone(event.data)


class TestQueryTurn(unittest.TestCase):
    def test_defaults(self) -> None:
        turn = QueryTurn()
        self.assertEqual(turn.turn_number, 0)
        self.assertEqual(turn.tool_uses, [])
        self.assertEqual(turn.text_content, "")
        self.assertEqual(turn.thinking_content, "")
        self.assertEqual(turn.stop_reason, "")

    def test_accumulate_text(self) -> None:
        turn = QueryTurn()
        turn.text_content += "hello "
        turn.text_content += "world"
        self.assertEqual(turn.text_content, "hello world")


class TestStreamingQueryState(unittest.TestCase):
    def test_initial_state(self) -> None:
        config = QueryConfig()
        context = MagicMock()
        state = StreamingQueryState(
            messages=[],
            system_prompt="test",
            tools=[],
            context=context,
            config=config,
        )
        self.assertEqual(state.turn_count, 0)
        self.assertFalse(state.is_done)
        self.assertEqual(state.compact_retries, 0)

    def test_total_usage(self) -> None:
        config = QueryConfig()
        context = MagicMock()
        state = StreamingQueryState(
            messages=[],
            system_prompt="test",
            tools=[],
            context=context,
            config=config,
        )
        self.assertEqual(state.total_usage.input_tokens, 0)
        self.assertEqual(state.total_usage.output_tokens, 0)


class TestStreamingQueryAbort(unittest.TestCase):
    def test_abort_yields_aborted_event(self) -> None:
        async def _run() -> None:
            config = QueryConfig(max_turns=5)
            context = MagicMock()
            abort = MagicMock()
            abort.aborted = True

            events: list[QueryEvent] = []
            async for event in streaming_query(
                messages=[],
                system_prompt="test",
                tools=[],
                context=context,
                config=config,
                abort_signal=abort,
            ):
                events.append(event)

            self.assertTrue(any(e.type == "aborted" for e in events))

        asyncio.run(_run())


class TestStreamingQueryMaxTurns(unittest.TestCase):
    def test_max_turns_zero_yields_complete(self) -> None:
        async def _run() -> None:
            config = QueryConfig(max_turns=0)
            context = MagicMock()

            events: list[QueryEvent] = []
            async for event in streaming_query(
                messages=[],
                system_prompt="test",
                tools=[],
                context=context,
                config=config,
            ):
                events.append(event)

            self.assertTrue(any(e.type == "query_complete" for e in events))

        asyncio.run(_run())


class TestQueryConfigNewFields(unittest.TestCase):
    def test_emit_tool_use_summaries_default(self) -> None:
        config = QueryConfig()
        self.assertTrue(config.emit_tool_use_summaries)

    def test_fast_mode_default(self) -> None:
        config = QueryConfig()
        self.assertFalse(config.fast_mode_enabled)


if __name__ == "__main__":
    unittest.main()
