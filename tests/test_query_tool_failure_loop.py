"""ch01/round3 acceptance tests: ``tool_failure_loop`` Terminal.

The query loop must stop with ``Terminal(reason="tool_failure_loop")``
when consecutive tool batches contain only repeating failures — mirroring
TS query.ts:1638-1666 (guard runs after the aborted_tools/hook_stopped
returns, before max_turns) backed by query/toolFailureLoopGuard.ts.

Python-specific note: an unknown tool produces a tool_result whose
content is the JSON string '{"error": "unknown tool: X"}'
(registry.py:104-111 via query.py json.dumps) — assertions here stay on
the stable surface (terminal reason + trip-message header), never on a
specific error-category name.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.agent_loop_compat import run_query_as_agent_loop
from src.query.query import QueryParams, run_query
from src.query.transitions import Terminal
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import ToolResultBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

TRIP_HEADER = "Stopped: repeated tool failures detected."


def _run(coro):
    return asyncio.run(coro)


def _tool_use_response(tool_use_id: str) -> ChatResponse:
    return ChatResponse(
        content="Trying the tool...",
        model="test",
        usage={"input_tokens": 10, "output_tokens": 20},
        finish_reason="tool_use",
        tool_uses=[{
            "id": tool_use_id,
            "name": "BogusTool",
            "input": {},
        }],
    )


def _completion_response() -> ChatResponse:
    return ChatResponse(
        content="Done.",
        model="test",
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _failing_result(tool_use_id: str) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content='{"error": "unknown tool: BogusTool"}',
                is_error=True,
            )
        ],
    )


def _ok_result(tool_use_id: str) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=tool_use_id, content="ok", is_error=False)
        ],
    )


class _FailingToolHarness:
    """Provider + patched tool runner that fail identically every turn."""

    def __init__(self):
        self.turn = 0
        self.provider = MagicMock()
        self.provider.chat_stream_response.side_effect = NotImplementedError()
        self.provider.chat.side_effect = self._next_response

    def _next_response(self, *args, **kwargs):
        self.turn += 1
        return _tool_use_response(f"toolu_{self.turn:03d}")

    async def run_tools(self, tool_use_blocks, *args, **kwargs):
        return [_failing_result(block.id) for block in tool_use_blocks]


def _make_params(*, workspace: Path, provider, max_turns: int = 10) -> QueryParams:
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
        max_turns=max_turns,
    )


class TestToolFailureLoopTerminal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repeated_failures_trip_after_default_threshold(self):
        harness = _FailingToolHarness()
        params = _make_params(workspace=self.workspace, provider=harness.provider)

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=harness.run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertIsInstance(terminal, Terminal)
        self.assertEqual(terminal.reason, "tool_failure_loop")
        # Default threshold 3 → exactly 3 model turns, not max_turns (10).
        self.assertEqual(harness.turn, 3)

        api_errors = [
            m for m in messages
            if isinstance(m, AssistantMessage)
            and getattr(m, "isApiErrorMessage", False)
        ]
        self.assertEqual(len(api_errors), 1)
        text = "".join(
            getattr(block, "text", "") for block in api_errors[0].content
        ) if isinstance(api_errors[0].content, list) else str(api_errors[0].content)
        self.assertIn(TRIP_HEADER, text)

    def test_threshold_zero_disables_guard(self):
        """CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD=0 → loop runs to
        max_turns instead (guard:59-61)."""
        harness = _FailingToolHarness()
        params = _make_params(
            workspace=self.workspace, provider=harness.provider, max_turns=4,
        )

        with patch.dict(
            "os.environ", {"CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD": "0"}
        ), patch(
            "src.query.query._run_tools_partitioned",
            side_effect=harness.run_tools,
        ):
            _messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "max_turns")

    def test_success_between_failures_resets_counters(self):
        """fail, fail, success, fail, fail, then completion → 'completed',
        never trips at default threshold 3 (guard:91-94)."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        turn_box = {"n": 0}

        def next_response(*args, **kwargs):
            turn_box["n"] += 1
            if turn_box["n"] <= 5:
                return _tool_use_response(f"toolu_{turn_box['n']:03d}")
            return _completion_response()

        provider.chat.side_effect = next_response

        async def run_tools(tool_use_blocks, *args, **kwargs):
            # Turn 3 succeeds; the rest fail identically.
            if turn_box["n"] == 3:
                return [_ok_result(b.id) for b in tool_use_blocks]
            return [_failing_result(b.id) for b in tool_use_blocks]

        params = _make_params(workspace=self.workspace, provider=provider)

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=run_tools,
        ):
            _messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(turn_box["n"], 6)


class TestCompatPathSurfacing(unittest.TestCase):
    """The adapter must surface the trip message as response_text while
    keeping it out of on_message (S1 contract)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_trip_message_surfaces_as_response_text(self):
        harness = _FailingToolHarness()
        seen_messages = []

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=harness.run_tools,
        ):
            result = _run(run_query_as_agent_loop(
                initial_messages=[UserMessage(content="do the thing")],
                provider=harness.provider,
                tool_registry=self.registry,
                tool_context=self.context,
                on_message=seen_messages.append,
            ))

        self.assertIn(TRIP_HEADER, result.response_text)
        # No exception, graceful stop (C1 contract).
        # S1 contract: the API-error message never reaches on_message.
        for msg in seen_messages:
            self.assertFalse(
                getattr(msg, "isApiErrorMessage", False),
                "isApiErrorMessage assistant message leaked into on_message",
            )


if __name__ == "__main__":
    unittest.main()
