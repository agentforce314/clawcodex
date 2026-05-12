"""Phase E acceptance tests: model fallback (E.1-E.3, E.5) and
continuation nudge (E.4).
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.query.continuation_signals import (
    MAX_CONTINUATION_NUDGES,
    matches_continuation_signal,
)
from src.query.query import QueryParams, run_query
from src.query.transitions import Terminal
from src.services.api.errors import FallbackTriggeredError
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(workspace: Path, provider: MagicMock, **kwargs) -> QueryParams:
    registry = build_default_registry()
    context = ToolContext(workspace_root=workspace)
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=context,
        provider=provider,
        abort_controller=AbortController(),
        max_turns=kwargs.pop("max_turns", 10),
        **kwargs,
    )


# --- E.4: continuation signal regex tests --------------------------


class TestContinuationSignals(unittest.TestCase):
    """Unit tests for the regex set in continuation_signals."""

    def test_so_now_i_need_to_action_matches(self):
        self.assertTrue(matches_continuation_signal("so now i need to write the file"))

    def test_now_ill_action_matches(self):
        self.assertTrue(matches_continuation_signal("now i'll create the file"))

    def test_let_me_action_matches(self):
        self.assertTrue(matches_continuation_signal("let me update the config"))

    def test_time_to_action_matches(self):
        self.assertTrue(matches_continuation_signal("time to begin"))

    def test_completion_marker_suppresses(self):
        self.assertFalse(matches_continuation_signal(
            "let me write the file. that's all done now."
        ))

    def test_explanatory_text_does_not_match(self):
        self.assertFalse(matches_continuation_signal(
            "the function will read the file and parse the contents"
        ))

    def test_short_pattern_only_for_short_text(self):
        self.assertTrue(matches_continuation_signal("i'll write the file."))
        long_text = "here is a long explanation. " * 5 + "i'll write the file."
        self.assertFalse(matches_continuation_signal(long_text))


# --- E.4: continuation nudge integration ---------------------------


class TestContinuationNudgeIntegration(unittest.TestCase):
    """The loop injects a nudge user message when a continuation
    signal fires; capped at MAX_CONTINUATION_NUDGES."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nudge_fires_on_continuation_signal(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="let me now write the file",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 6},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 8, "output_tokens": 2},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        params = _make_params(self.workspace, provider)
        _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)

        second_call_messages = provider.chat.call_args_list[1].args[0]
        nudges = [
            msg for msg in second_call_messages
            if msg.get("role") == "user"
            and "Continue with the task" in str(msg.get("content", ""))
        ]
        self.assertGreaterEqual(len(nudges), 1)

    def test_nudge_capped_at_max(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="let me now write the file",
            model="test",
            usage={"input_tokens": 5, "output_tokens": 6},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(
            self.workspace, provider, max_turns=MAX_CONTINUATION_NUDGES + 5,
        )
        _, terminal = _run(run_query(params))

        self.assertEqual(
            provider.chat.call_count, MAX_CONTINUATION_NUDGES + 1,
            f"Expected {MAX_CONTINUATION_NUDGES + 1} calls; got {provider.chat.call_count}",
        )
        self.assertEqual(terminal.reason, "completed")

    def test_completion_marker_suppresses_nudge(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="let me show you the result. all done.",
            model="test",
            usage={"input_tokens": 5, "output_tokens": 8},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(self.workspace, provider)
        _, terminal = _run(run_query(params))

        self.assertEqual(provider.chat.call_count, 1)
        self.assertEqual(terminal.reason, "completed")


# --- E.1-E.2-E.5: model fallback integration -----------------------


class TestModelFallback(unittest.TestCase):
    """When FallbackTriggeredError is raised and params.fallback_model
    is set, the loop switches model and retries the request."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fallback_switches_model_and_retries(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            FallbackTriggeredError(
                original_model="claude-opus-4-7",
                fallback_model="claude-sonnet-4-6",
            ),
            ChatResponse(
                content="Done from fallback.",
                model="claude-sonnet-4-6",
                usage={"input_tokens": 5, "output_tokens": 3},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        params = _make_params(
            self.workspace, provider, fallback_model="claude-sonnet-4-6",
        )
        messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)

        system_msgs = [
            m for m in messages
            if isinstance(m, SystemMessage)
            and "Switched to" in str(m.content)
        ]
        self.assertGreaterEqual(len(system_msgs), 1)

        second_call = provider.chat.call_args_list[1]
        passed_model = second_call.kwargs.get("model")
        self.assertEqual(passed_model, "claude-sonnet-4-6")

    def test_no_fallback_model_propagates_error(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = FallbackTriggeredError(
            original_model="opus",
            fallback_model="sonnet",
        )

        params = _make_params(self.workspace, provider)
        _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "model_error")
        self.assertIsInstance(terminal.error, FallbackTriggeredError)


if __name__ == "__main__":
    unittest.main()
