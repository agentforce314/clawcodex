"""Ch5/B.3 — ContextCollapseStore staging + recover_from_overflow tests.

Verifies the contracts:
  * `add_staged` records proposed collapses without applying them.
  * `project_view` does NOT honor staged collapses (only commits).
  * `drain_staged` promotes staged → commits and returns the count.
  * `recover_from_overflow` drains the store and reprojects the
    messages on a real overflow; returns DrainResult(committed=0)
    when the store is empty/disabled/None.
  * The query loop's PTL recovery path calls `recover_from_overflow`
    BEFORE `reactive_compact`, so a successful drain short-circuits
    the more expensive reactive-compact LLM call.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage
from src.types.content_blocks import TextBlock
from src.utils.abort_controller import AbortController

from src.services.compact.context_collapse import (
    ContextCollapseStore,
    CollapseCommit,
    DrainResult,
    recover_from_overflow,
    set_context_collapse_store,
)
from src.query.query import QueryParams, query
from src.query.transitions import TerminalHolder


def _run(coro):
    return asyncio.run(coro)


class TestContextCollapseStaging(unittest.TestCase):
    """B.3 — staging API on ContextCollapseStore."""

    def test_add_staged_does_not_affect_project_view(self):
        """A staged collapse is NOT honored by project_view until
        drained."""
        msg_uuid = "a-b-c"
        msg = UserMessage(content="hello", uuid=msg_uuid)
        store = ContextCollapseStore()
        store.add_staged([msg_uuid], "stand-in summary")

        # project_view leaves the message untouched.
        view = store.project_view([msg])
        self.assertEqual(len(view), 1)
        self.assertEqual(view[0].uuid, msg_uuid)

    def test_drain_staged_promotes_to_commits(self):
        msg_uuid = "uuid-1"
        msg = UserMessage(content="hello", uuid=msg_uuid)
        store = ContextCollapseStore()
        store.add_staged([msg_uuid], "stand-in summary")

        n = store.drain_staged()
        self.assertEqual(n, 1)
        self.assertEqual(len(store.commits), 1)
        self.assertEqual(len(store.staged), 0)

        # Now project_view honors the (formerly staged) collapse.
        view = store.project_view([msg])
        self.assertEqual(len(view), 1)
        # The summary message replaces the original.
        self.assertTrue(getattr(view[0], "isVirtual", False))

    def test_drain_staged_returns_zero_when_empty(self):
        store = ContextCollapseStore()
        self.assertEqual(store.drain_staged(), 0)


class TestRecoverFromOverflow(unittest.TestCase):
    """B.3 — recover_from_overflow helper function."""

    def tearDown(self):
        # Reset the module-level global so tests don't bleed state.
        set_context_collapse_store(None)

    def test_no_store_returns_zero(self):
        set_context_collapse_store(None)
        msg = UserMessage(content="hi", uuid="x")
        result = recover_from_overflow([msg], "repl_main_thread")
        self.assertIsInstance(result, DrainResult)
        self.assertEqual(result.committed, 0)
        self.assertEqual(result.messages, [msg])

    def test_disabled_store_returns_zero(self):
        store = ContextCollapseStore()
        store.enabled = False
        store.add_staged(["x"], "summary")
        set_context_collapse_store(store)

        msg = UserMessage(content="hi", uuid="x")
        result = recover_from_overflow([msg], "repl_main_thread")
        self.assertEqual(result.committed, 0)
        # The staged item is NOT drained when disabled.
        self.assertEqual(len(store.staged), 1)
        self.assertEqual(len(store.commits), 0)

    def test_empty_staged_returns_zero(self):
        store = ContextCollapseStore()
        set_context_collapse_store(store)
        msg = UserMessage(content="hi", uuid="x")
        result = recover_from_overflow([msg], "repl_main_thread")
        self.assertEqual(result.committed, 0)

    def test_drains_and_reprojects(self):
        msg_uuid = "msg-1"
        msg = UserMessage(content="long history", uuid=msg_uuid)
        store = ContextCollapseStore()
        store.add_staged([msg_uuid], "[summary]")
        set_context_collapse_store(store)

        result = recover_from_overflow([msg], "repl_main_thread")
        self.assertEqual(result.committed, 1)
        # The message was replaced by the summary in the projected view.
        self.assertEqual(len(result.messages), 1)
        self.assertTrue(getattr(result.messages[0], "isVirtual", False))


class TestCollapseDrainInLoop(unittest.TestCase):
    """B.3 integration — query() drains staged collapses BEFORE
    reactive_compact on a PTL recovery."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()
        set_context_collapse_store(None)

    def test_drain_runs_before_reactive_compact(self):
        """When the model raises PTL and the collapse store has staged
        commits, the loop drains FIRST. If the drained projection then
        successfully fits, reactive_compact does NOT need to fire."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            Exception("Prompt is too long"),  # turn 1 — triggers drain
            ChatResponse(  # turn 2 (post-drain): OK
                content="Done.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        # Seed the global store with a staged collapse so the drain
        # has something to promote.
        msg_uuid = "long-msg-1"
        store = ContextCollapseStore()
        store.add_staged([msg_uuid], "[collapsed history]")
        set_context_collapse_store(store)

        # Mock reactive_compact so we can assert it was NOT called.
        reactive_calls = []

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            reactive_calls.append(1)
            from src.services.compact.reactive_compact import (
                ReactiveCompactResult,
            )
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="reactive summary")],
                tokens_before=0,
            )

        # Build an initial messages list that includes the message we
        # staged a collapse for.
        params = QueryParams(
            messages=[UserMessage(content="long history", uuid=msg_uuid)],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
        )
        holder = TerminalHolder()

        # Override config to enable context-collapse for this test.
        from src.query.config import FrozenQueryConfig
        cfg = FrozenQueryConfig(
            context_collapse_enabled=True,
            reactive_compact_enabled=True,
        )

        async def run():
            with patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ), patch(
                "src.query.query.build_query_config",
                return_value=cfg,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        # The loop should have:
        #   1. Hit PTL on turn 1.
        #   2. Drained the staged collapse, set transition=
        #      collapse_drain_retry, retried.
        #   3. Turn 2 succeeded with the drained-projection messages.
        # reactive_compact was NEVER called because the drain
        # succeeded on the first overflow.
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)
        self.assertEqual(
            len(reactive_calls), 0,
            "Drain should short-circuit reactive_compact when staged "
            "commits were drained successfully",
        )

    def test_drain_oneshot_then_fall_through_to_reactive_compact(self):
        """If the drain runs once and the post-drain retry STILL
        raises PTL, the loop falls through to reactive_compact (the
        drain is one-shot per recovery attempt)."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            Exception("Prompt is too long"),  # turn 1 — triggers drain
            Exception("Prompt is too long"),  # turn 2 (post-drain) — still PTL
            ChatResponse(  # turn 3 (post-reactive-compact): OK
                content="Done.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        msg_uuid = "long-msg-1"
        store = ContextCollapseStore()
        store.add_staged([msg_uuid], "[collapsed history]")
        set_context_collapse_store(store)

        reactive_calls = []

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            reactive_calls.append(1)
            from src.services.compact.reactive_compact import (
                ReactiveCompactResult,
            )
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="reactive summary")],
                tokens_before=0,
            )

        params = QueryParams(
            messages=[UserMessage(content="long history", uuid=msg_uuid)],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
        )
        holder = TerminalHolder()

        from src.query.config import FrozenQueryConfig
        cfg = FrozenQueryConfig(
            context_collapse_enabled=True,
            reactive_compact_enabled=True,
        )

        async def run():
            with patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ), patch(
                "src.query.query.build_query_config",
                return_value=cfg,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        # 3 model calls: PTL → drain → PTL → reactive_compact → OK.
        self.assertEqual(provider.chat.call_count, 3)
        # reactive_compact called exactly once (after drain
        # one-shot exhausted).
        self.assertEqual(len(reactive_calls), 1)
        self.assertEqual(holder.value.reason, "completed")


if __name__ == "__main__":
    unittest.main()
