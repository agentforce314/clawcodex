"""Phase A acceptance tests: typed Terminal return values via the
``run_query()`` helper and the ``TerminalHolder`` protocol.

Verifies the Phase A foundation — the 4 terminal reasons reachable
on the un-extended loop body:

  * completed (normal completion)
  * max_turns (max_turns limit hit)
  * aborted_streaming (abort during model call)
  * aborted_tools (abort during tool execution)
  * model_error (unrecoverable exception in the model call)

The remaining 5 reasons (prompt_too_long, image_error,
blocking_limit, stop_hook_prevented, hook_stopped) are reachable
only after later phases land their recovery / hook / guard
infrastructure.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import (
    QueryParams,
    StreamEvent,
    run_query,
)
from src.query.transitions import Terminal, TerminalHolder
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(
    *,
    workspace: Path,
    provider: MagicMock,
    abort: AbortController | None = None,
    max_turns: int = 10,
) -> QueryParams:
    registry = build_default_registry()
    context = ToolContext(workspace_root=workspace)
    return QueryParams(
        messages=[UserMessage(content="Hi")],
        system_prompt="You are helpful.",
        tools=registry.list_tools(),
        tool_registry=registry,
        tool_use_context=context,
        provider=provider,
        abort_controller=abort or AbortController(),
        max_turns=max_turns,
    )


class TestTerminalCompleted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_turn_returns_completed(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello, world!",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(workspace=self.workspace, provider=provider)
        messages, terminal = _run(run_query(params))
        self.assertIsInstance(terminal, Terminal)
        self.assertEqual(terminal.reason, "completed")


class TestTerminalMaxTurns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_max_turns_returns_max_turns_terminal(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Working...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "x.txt"),
                    "content": "hi",
                },
            }],
        )

        params = _make_params(workspace=self.workspace, provider=provider, max_turns=2)
        messages, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "max_turns")
        self.assertEqual(terminal.turn_count, 3)


class TestTerminalAbortedStreaming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_abort_before_response_returns_aborted_streaming(self):
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

        params = _make_params(workspace=self.workspace, provider=provider, abort=abort)
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "aborted_streaming")


class TestTerminalAbortedTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_abort_during_tools_returns_aborted_tools(self):
        # Abort needs to fire AFTER the model call's abort check
        # but BEFORE the post-tool abort check. The model call itself
        # must return normally; the abort is raised from within tool
        # execution. We patch `_run_tools_partitioned` to set the
        # abort signal as a side-effect during execution.
        abort = AbortController()
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="I'll write the file.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_001",
                "name": "Write",
                "input": {
                    "file_path": str(self.workspace / "x.txt"),
                    "content": "hi",
                },
            }],
        )

        params = _make_params(workspace=self.workspace, provider=provider, abort=abort)

        async def aborting_runner(*args, **kwargs):
            from src.types.content_blocks import ToolResultBlock
            abort.abort("user_abort")
            return [
                UserMessage(content=[
                    ToolResultBlock(
                        tool_use_id="toolu_001",
                        content="aborted",
                        is_error=True,
                    ),
                ]),
            ]

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=aborting_runner,
        ):
            _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "aborted_tools")


class TestTerminalModelError(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unrecoverable_model_error_returns_model_error(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = RuntimeError("network exploded")

        params = _make_params(workspace=self.workspace, provider=provider)
        _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "model_error")
        self.assertIsNotNone(terminal.error)


class TestTerminalHolderDirectUsage(unittest.TestCase):
    """Streaming consumers can pass their own TerminalHolder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_holder_value_set_after_async_for_loop(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hi back.",
            model="test",
            usage={"input_tokens": 5, "output_tokens": 3},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = _make_params(workspace=self.workspace, provider=provider)
        holder = TerminalHolder()

        async def consume():
            from src.query.query import query as query_gen
            async for _ in query_gen(params, terminal_holder=holder):
                pass

        _run(consume())
        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "completed")


if __name__ == "__main__":
    unittest.main()
