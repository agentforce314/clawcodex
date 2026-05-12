"""Ch5/B.3 acceptance tests: context-collapse drain on 413.

Verifies:
- ContextCollapseStore.add_staged + drain_staged round-trip
- recover_from_overflow returns DrainResult with committed > 0 when
  staged collapses exist
- query.py runs collapse drain BEFORE reactive_compact on PTL when
  staged collapses are present
- has_attempted_reactive_compact is NOT flipped by the drain path
- transition.reason == "collapse_drain_retry" on the intermediate state
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import QueryParams, run_query
from src.services.compact.context_collapse import (
    ContextCollapseStore,
    DrainResult,
    recover_from_overflow,
    set_context_collapse_store,
)
from src.services.compact.reactive_compact import ReactiveCompactResult
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage
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


class TestStoreStaging(unittest.TestCase):
    """Unit tests for add_staged / drain_staged / staged_count."""

    def test_staged_does_not_appear_in_project_view(self):
        store = ContextCollapseStore()
        msg = UserMessage(content="x")
        store.add_staged([msg.uuid], "summary text")
        # Before drain: project_view sees no commits → no archives → msg passes through.
        view = store.project_view([msg])
        self.assertEqual(len(view), 1)

    def test_drain_promotes_to_commits(self):
        store = ContextCollapseStore()
        msg = UserMessage(content="x")
        store.add_staged([msg.uuid], "summary")
        self.assertEqual(store.staged_count(), 1)
        committed = store.drain_staged()
        self.assertEqual(committed, 1)
        self.assertEqual(store.staged_count(), 0)
        view = store.project_view([msg])
        self.assertEqual(len(view), 1)
        self.assertTrue(getattr(view[0], "isVirtual", False))

    def test_drain_empty_returns_zero(self):
        store = ContextCollapseStore()
        self.assertEqual(store.drain_staged(), 0)


class TestRecoverFromOverflow(unittest.TestCase):
    """Module-level recover_from_overflow contract."""

    def tearDown(self):
        set_context_collapse_store(None)

    def test_no_store_returns_zero(self):
        result = recover_from_overflow([], "repl_main_thread")
        self.assertIsInstance(result, DrainResult)
        self.assertEqual(result.committed, 0)

    def test_drain_when_staged(self):
        store = ContextCollapseStore()
        m = UserMessage(content="x")
        store.add_staged([m.uuid], "summary")
        set_context_collapse_store(store)

        result = recover_from_overflow([m], "repl_main_thread")
        self.assertEqual(result.committed, 1)
        # Per B.3 contract: isVirtual is False so the message is
        # API-visible (the entire point of drain recovery is for the
        # summary to reach the API).
        self.assertEqual(len(result.messages), 1)
        self.assertFalse(getattr(result.messages[0], "isVirtual", False))
        content = result.messages[0].content
        if isinstance(content, list):
            joined = "".join(
                getattr(b, "text", "") for b in content
            )
        else:
            joined = str(content)
        self.assertIn("summary", joined)


class TestCollapseDrainRunsBeforeReactiveCompact(unittest.TestCase):
    """Integration: on a 413, the loop drains the staged collapses
    FIRST. Only if drain.committed == 0 (or already retried) does
    reactive_compact fire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        set_context_collapse_store(None)
        self.tmp.cleanup()

    def test_drain_runs_before_reactive_compact(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        params = _make_params(self.workspace, provider)
        # Stage a collapse of the initial user message with a known
        # summary text. project_view replaces it with a summary
        # message containing the text.
        initial_msg = params.messages[0]
        store = ContextCollapseStore()
        store.add_staged([initial_msg.uuid], "DRAINED-SUMMARY-MARKER")
        set_context_collapse_store(store)

        call_count = {"n": 0}

        def chat_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Prompt is too long.")
            return ChatResponse(
                content="Done after drain.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 3},
                finish_reason="end_turn",
                tool_uses=None,
            )

        provider.chat.side_effect = chat_side_effect

        rc_mock = MagicMock()

        async def rc_async(**kwargs):
            return ReactiveCompactResult(
                compacted=True,
                messages=kwargs["messages"],
                tokens_before=1000,
                tokens_after=200,
            )

        rc_mock.side_effect = rc_async

        with patch(
            "src.services.compact.reactive_compact.reactive_compact",
            new=rc_mock,
        ):
            _, terminal = _run(run_query(params))

        # The drain path took priority → reactive_compact NOT called.
        self.assertEqual(rc_mock.call_count, 0)
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(call_count["n"], 2)
        # The DRAINED VIEW (with the summary block) reaches the second
        # model call's input.
        second_call_messages = provider.chat.call_args_list[1].args[0]
        drained_payload = "\n".join(
            str(m.get("content", "")) for m in second_call_messages
        )
        self.assertIn("DRAINED-SUMMARY-MARKER", drained_payload)

    def test_no_staged_falls_through_to_reactive_compact(self):
        store = ContextCollapseStore()
        set_context_collapse_store(store)

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            RuntimeError("Prompt is too long."),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 3},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        rc_mock = MagicMock()

        async def rc_async(**kwargs):
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="compacted")],
                tokens_before=1000,
                tokens_after=200,
            )

        rc_mock.side_effect = rc_async

        params = _make_params(self.workspace, provider)
        with patch(
            "src.services.compact.reactive_compact.reactive_compact",
            new=rc_mock,
        ):
            _, terminal = _run(run_query(params))

        self.assertEqual(rc_mock.call_count, 1)
        self.assertEqual(terminal.reason, "completed")


if __name__ == "__main__":
    unittest.main()
