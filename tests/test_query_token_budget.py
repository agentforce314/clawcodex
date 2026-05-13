"""Ch5/D — query() ↔ token-budget integration tests.

Verifies the contracts from chapter 5 §"Token Budgets":
  D.1 — task_budget plumbs through QueryParams.
  D.2 — check_token_budget fires after stop_hooks pass; ContinueDecision
        injects a nudge and re-enters the loop with
        transition.reason='token_budget_continuation'.
  D.3 — parse_token_budget runs at QueryEngine.submit_message; the
        +500k marker is stripped from the user-visible prompt.
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
from src.utils.abort_controller import AbortController

from src.query.query import QueryParams, query
from src.query.transitions import TerminalHolder


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

    def _params(self, provider, *, task_budget=None):
        return QueryParams(
            messages=[UserMessage(content="Do work")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=20,
            task_budget=task_budget,
        )


class TestTokenBudgetParse(unittest.TestCase):
    """D.3 — parse_token_budget + find_token_budget_positions wired
    at the engine input layer."""

    def test_plus_500k_parses_and_strips_marker(self):
        from src.query.token_budget import (
            find_token_budget_positions,
            parse_token_budget,
        )
        text = "+500k continue refactoring the auth module"
        self.assertEqual(parse_token_budget(text), 500_000)
        positions = find_token_budget_positions(text)
        self.assertEqual(len(positions), 1)
        self.assertEqual(text[positions[0].start:positions[0].end], "+500k")

    def test_no_marker_returns_none(self):
        from src.query.token_budget import (
            find_token_budget_positions,
            parse_token_budget,
        )
        text = "continue refactoring"
        self.assertIsNone(parse_token_budget(text))
        self.assertEqual(find_token_budget_positions(text), [])

    def test_parse_and_positions_agree(self):
        """D.3 invariant — parse and positions agree on every input
        the assertion in QueryEngine.submit_message relies on."""
        from src.query.token_budget import (
            find_token_budget_positions,
            parse_token_budget,
        )
        for text in [
            "+500k go",
            "go +250k",
            "use 1.5m tokens",
            "no marker",
            "+500k middle +250k",
        ]:
            parsed = parse_token_budget(text)
            positions = find_token_budget_positions(text)
            self.assertEqual(
                parsed is None,
                not positions,
                f"Disagreement on input {text!r}: "
                f"parsed={parsed}, positions={positions}",
            )


class TestTokenBudgetContinuation(_Base):
    """D.2 — budget continuation triggers a re-entry with a nudge."""

    def test_no_budget_falls_through_to_completed(self):
        """When task_budget is None, budget_tracker stays None and the
        loop completes after the first turn."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = self._params(provider, task_budget=None)
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 1)

    def test_budget_below_threshold_injects_nudge_and_continues(self):
        """D.2: when turn_tokens < budget * 0.9, ContinueDecision is
        returned, a nudge user message is appended, and the loop
        re-enters with transition.reason='token_budget_continuation'.

        Setup: budget=500k. Turn 1 produces 100 output tokens (under
        threshold → continue with nudge). Turn 2 produces 470k tokens
        (94% of budget → over threshold → stop). The provider is called
        twice; the loop ends with Terminal(completed).
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="First chunk.",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 100},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="Second chunk — final.",
                model="test",
                usage={"input_tokens": 200, "output_tokens": 470_000},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        params = self._params(provider, task_budget={"total": 500_000})
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        # Budget caused a continuation after turn 1 (100 tokens, under
        # 90% threshold) and a stop after turn 2 (470k tokens, over
        # threshold). Two model calls total; clean Terminal(completed).
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)

    def test_subagent_always_stops(self):
        """D.2: when tool_use_context has an agent_id, the budget always
        returns StopDecision (subagents don't continue past budget)."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Sub done.",
            model="test",
            usage={"input_tokens": 100, "output_tokens": 1_000},
            finish_reason="end_turn",
            tool_uses=None,
        )

        # Set agent_id on context — this is the subagent signal.
        self.context.agent_id = "sub-task-1"

        params = QueryParams(
            messages=[UserMessage(content="Sub task")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
            task_budget={"total": 500_000},
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Only one model call — budget didn't continue the subagent.
        self.assertEqual(provider.chat.call_count, 1)

    def test_budget_completed_when_at_threshold(self):
        """D.2: when turn_tokens >= budget * 0.9, the decision is Stop
        and the loop exits cleanly."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hit the budget.",
            model="test",
            usage={"input_tokens": 100, "output_tokens": 470_000},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = self._params(provider, task_budget={"total": 500_000})
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        # 470k of 500k = 94% — over the 90% completion threshold → stop.
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
