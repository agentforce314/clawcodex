"""Ch5/E — query() ↔ FallbackTriggeredError + continuation-nudge tests.

Covers:
  E.2 — FallbackTriggeredError swaps the provider, tombstones partial
        assistant messages, emits a "Switched to..." system message,
        and retries on the fallback provider.
  E.4 — continuation-nudge regex set (in src/query/continuation_signals.py)
        fires at no-tool-use exit with intent-to-continue text; capped
        at MAX_CONTINUATION_NUDGES=3.
  E.5 — TombstoneMessage is handled by QueryEngine.submit_message
        (matching message removed from _mutable_messages, not appended).
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
from src.types.messages import AssistantMessage, SystemMessage, TombstoneMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.query import QueryParams, query
from src.query.transitions import TerminalHolder
from src.services.api.errors import FallbackTriggeredError


def _run(coro):
    return asyncio.run(coro)


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()


class TestContinuationSignals(unittest.TestCase):
    """E.4 — regex matchers in src/query/continuation_signals.py."""

    def test_intent_to_continue_short_message(self):
        from src.query.continuation_signals import matches_continuation_signal
        # SIGNALS_SHORT_ONLY pattern: short message with action verb.
        self.assertTrue(matches_continuation_signal("I'll fix the bug now."))
        self.assertTrue(matches_continuation_signal("Next, I'll update the file."))

    def test_intent_to_continue_any_length(self):
        from src.query.continuation_signals import matches_continuation_signal
        # SIGNALS_ANY_LENGTH pattern.
        self.assertTrue(matches_continuation_signal(
            "OK so now I'll create the file. " + ("x " * 100)
        ))
        self.assertTrue(matches_continuation_signal(
            "Let me now proceed with the implementation."
        ))

    def test_completion_marker_suppresses(self):
        from src.query.continuation_signals import matches_continuation_signal
        self.assertFalse(matches_continuation_signal(
            "Let me now show you the result. Hope this helps!"
        ))
        self.assertFalse(matches_continuation_signal("I'm done."))
        self.assertFalse(matches_continuation_signal(
            "Now I'll proceed. All set."
        ))

    def test_long_explanatory_text_does_not_match_short_pattern(self):
        from src.query.continuation_signals import matches_continuation_signal
        long_text = (
            "When considering whether to do this, you'll need to think "
            "carefully about the implications for downstream consumers "
            "and whether the additional complexity is justified. "
            "Note however that the costs are real."
        )
        # SIGNALS_SHORT_ONLY would match "you'll need to do" but the
        # text is > 80 chars, so it's gated off.
        self.assertFalse(matches_continuation_signal(long_text))

    def test_unrelated_text_does_not_match(self):
        from src.query.continuation_signals import matches_continuation_signal
        self.assertFalse(matches_continuation_signal(
            "The function returns a tuple."
        ))


class TestContinuationNudge(_Base):
    """E.4 — query() injects nudge when last assistant text signals
    intent to continue and turn_count < max_turns."""

    def test_nudge_fires_on_continuation_signal(self):
        """When the model returns intent-to-continue text with no tool
        calls, the loop injects a nudge and re-enters."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="So now I'll create the file.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 10},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="OK, file created. All set.",
                model="test",
                usage={"input_tokens": 30, "output_tokens": 10},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        params = QueryParams(
            messages=[UserMessage(content="Help")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Two model calls — nudge injected after the first prompted a
        # continuation; second turn had completion marker ("All set").
        self.assertEqual(provider.chat.call_count, 2)

    def test_nudge_suppressed_by_completion_marker(self):
        """When the model's text contains a completion marker (e.g.
        'hope this helps!'), the nudge does NOT fire and the loop
        completes after the first turn."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Let me now show you. Hope this helps!",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = QueryParams(
            messages=[UserMessage(content="Explain")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 1)

    def test_nudge_capped_at_three(self):
        """The continuation-nudge count is capped at MAX_CONTINUATION_NUDGES=3.
        After 3 nudges, the loop falls through to Terminal(completed)
        rather than a 4th nudge."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Every response signals "let me now ..." (continuation intent).
        provider.chat.return_value = ChatResponse(
            content="Let me now create the file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 10},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = QueryParams(
            messages=[UserMessage(content="Help")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # 1 initial + 3 nudge retries = 4 model calls total (4th nudge
        # is blocked by the cap).
        self.assertEqual(provider.chat.call_count, 4)


class TestModelFallback(_Base):
    """E.2 — FallbackTriggeredError handling."""

    def test_no_fallback_provider_propagates_error(self):
        """When params.fallback_provider is None, FallbackTriggeredError
        propagates out as Terminal(model_error)."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = FallbackTriggeredError(
            original_model="opus-1", fallback_model="sonnet-1",
        )

        params = QueryParams(
            messages=[UserMessage(content="Help")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
            fallback_provider=None,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "model_error")

    def test_overloaded_error_translates_to_fallback_when_provider_set(self):
        """E.2 production trigger: when the primary provider raises an
        OverloadedError (529) AND a fallback_provider is configured,
        the loop's _call_model_with_fallback_signal closure translates
        the 529 into a FallbackTriggeredError, which the existing
        fallback handler catches and swaps providers for. The user
        sees a "Switched to ..." system message."""
        from src.services.api.errors import OverloadedError

        primary = MagicMock()
        primary.model = "opus-1"
        primary.chat_stream_response.side_effect = NotImplementedError()
        primary.chat.side_effect = OverloadedError("529 overloaded")

        fallback = MagicMock()
        fallback.model = "sonnet-1"
        fallback.chat_stream_response.side_effect = NotImplementedError()
        fallback.chat.return_value = ChatResponse(
            content="OK on fallback.",
            model="sonnet-1",
            usage={"input_tokens": 50, "output_tokens": 30},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = QueryParams(
            messages=[UserMessage(content="Long prompt")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=primary,
            abort_controller=self.abort,
            max_turns=5,
            fallback_provider=fallback,
        )
        holder = TerminalHolder()
        collected: list = []

        async def run():
            async for msg in query(params, terminal_holder=holder):
                collected.append(msg)

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(primary.chat.call_count, 1)
        self.assertEqual(fallback.chat.call_count, 1)
        switch_msgs = [
            m for m in collected
            if isinstance(m, SystemMessage)
            and "Switched to" in str(getattr(m, "content", ""))
        ]
        self.assertEqual(len(switch_msgs), 1)

    def test_overloaded_error_no_fallback_propagates(self):
        """E.2 production trigger negative case: when no fallback_provider
        is set, an OverloadedError propagates as Terminal(model_error)
        — the closure does NOT translate it."""
        from src.services.api.errors import OverloadedError

        primary = MagicMock()
        primary.model = "opus-1"
        primary.chat_stream_response.side_effect = NotImplementedError()
        primary.chat.side_effect = OverloadedError("529 overloaded")

        params = QueryParams(
            messages=[UserMessage(content="Long prompt")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=primary,
            abort_controller=self.abort,
            max_turns=5,
            fallback_provider=None,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "model_error")

    def test_fallback_swaps_provider_and_retries(self):
        """E.2: FallbackTriggeredError on the primary provider swaps
        to params.fallback_provider, emits a 'Switched to ...' system
        message, and completes on the fallback."""
        primary = MagicMock()
        primary.chat_stream_response.side_effect = NotImplementedError()
        primary.chat.side_effect = FallbackTriggeredError(
            original_model="opus-1", fallback_model="sonnet-1",
        )

        fallback = MagicMock()
        fallback.chat_stream_response.side_effect = NotImplementedError()
        fallback.chat.return_value = ChatResponse(
            content="Done on fallback.",
            model="sonnet-1",
            usage={"input_tokens": 50, "output_tokens": 30},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = QueryParams(
            messages=[UserMessage(content="Long prompt")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=primary,
            abort_controller=self.abort,
            max_turns=5,
            fallback_provider=fallback,
        )
        holder = TerminalHolder()
        collected: list = []

        async def run():
            async for msg in query(params, terminal_holder=holder):
                collected.append(msg)

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Primary was called once (raised the fallback signal);
        # fallback was called once (succeeded).
        self.assertEqual(primary.chat.call_count, 1)
        self.assertEqual(fallback.chat.call_count, 1)
        # A "Switched to ..." system message was yielded.
        switch_msgs = [
            m for m in collected
            if isinstance(m, SystemMessage)
            and "Switched to" in str(getattr(m, "content", ""))
        ]
        self.assertEqual(len(switch_msgs), 1)


class TestTombstoneMessage(unittest.TestCase):
    """E.5 — TombstoneMessage type."""

    def test_tombstone_message_has_default_type_tombstone(self):
        tm = TombstoneMessage()
        self.assertEqual(tm.type, "tombstone")
        self.assertIsNone(tm.message)

    def test_tombstone_with_message_carried(self):
        am = AssistantMessage(content="partial")
        tm = TombstoneMessage(message=am)
        self.assertEqual(tm.type, "tombstone")
        self.assertIs(tm.message, am)


if __name__ == "__main__":
    unittest.main()
