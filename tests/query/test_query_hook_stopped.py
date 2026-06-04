"""Ch5/round2 acceptance tests: ``hook_stopped`` Terminal mapping.

When a ``PreToolUse`` or ``PostToolUse`` hook sets
``prevent_continuation``, the tool-execution path emits an
``AttachmentMessage`` whose attachment's ``type`` is
``hook_stopped_continuation``. The query loop must detect this marker
after the tool batch completes and exit with
``Terminal(reason='hook_stopped')`` — mirroring TS query.ts:1540-1545
(flag set) and :1698-1701 (terminal return).

Three cases:
  * Positive — hook_stopped attachment in tool_results → ``hook_stopped``.
  * Negative — only normal tool_results → ``completed`` after second turn.
  * Abort wins — abort signal AND hook_stopped attachment → ``aborted_tools``.
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
    _is_hook_stopped_continuation,
    run_query,
)
from src.query.transitions import Terminal
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import ToolResultBlock
from src.types.messages import (
    AttachmentMessage,
    AssistantMessage,
    UserMessage,
    create_attachment_message,
)
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


def _make_tool_use_response(
    *, tool_use_id: str, workspace: Path,
) -> ChatResponse:
    return ChatResponse(
        content="Working on it...",
        model="test",
        usage={"input_tokens": 10, "output_tokens": 20},
        finish_reason="tool_use",
        tool_uses=[{
            "id": tool_use_id,
            "name": "Write",
            "input": {
                "file_path": str(workspace / "x.txt"),
                "content": "hi",
            },
        }],
    )


def _make_completion_response() -> ChatResponse:
    return ChatResponse(
        content="Done.",
        model="test",
        usage={"input_tokens": 10, "output_tokens": 5},
        finish_reason="end_turn",
        tool_uses=None,
    )


def _make_tool_result(tool_use_id: str) -> UserMessage:
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content="ok",
                is_error=False,
            ),
        ],
    )


def _make_hook_stopped_attachment(
    *,
    tool_use_id: str = "toolu_001",
    hook_name: str = "PreToolUse:Write",
    message: str = "Execution stopped by policy hook",
) -> AttachmentMessage:
    """Build an AttachmentMessage shaped exactly as
    ``tool_execution.py:362-372`` and ``tool_hooks.py:185-195`` produce.
    """
    return create_attachment_message({
        "type": "hook_stopped_continuation",
        "message": message,
        "hook_name": hook_name,
        "tool_use_id": tool_use_id,
        "hook_event": "PreToolUse",
    })


class TestIsHookStoppedContinuationPredicate(unittest.TestCase):
    """Unit-test the detection helper in isolation."""

    def test_attachment_with_hook_stopped_returns_true(self):
        msg = _make_hook_stopped_attachment()
        self.assertTrue(_is_hook_stopped_continuation(msg))

    def test_attachment_with_other_type_returns_false(self):
        msg = create_attachment_message({
            "type": "hook_blocking_error",
            "hook_name": "PostToolUse:Bash",
            "tool_use_id": "toolu_001",
            "hook_event": "PostToolUse",
            "blocking_error": "lint failed",
        })
        self.assertFalse(_is_hook_stopped_continuation(msg))

    def test_attachment_with_no_attachments_returns_false(self):
        msg = AttachmentMessage(attachments=[])
        self.assertFalse(_is_hook_stopped_continuation(msg))

    def test_regular_user_message_returns_false(self):
        msg = UserMessage(content="hi")
        self.assertFalse(_is_hook_stopped_continuation(msg))

    def test_assistant_message_returns_false(self):
        msg = AssistantMessage(content="hi")
        self.assertFalse(_is_hook_stopped_continuation(msg))

    def test_none_returns_false(self):
        self.assertFalse(_is_hook_stopped_continuation(None))

    def test_attachment_with_non_dict_attachment_returns_false(self):
        msg = AttachmentMessage(attachments=[object()])  # type: ignore[list-item]
        self.assertFalse(_is_hook_stopped_continuation(msg))


class TestHookStoppedTerminal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hook_stopped_attachment_exits_with_hook_stopped_terminal(self):
        """Positive case: the loop sees a hook_stopped_continuation
        attachment in tool_results and exits with the typed terminal.
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _make_tool_use_response(
            tool_use_id="toolu_001", workspace=self.workspace,
        )

        params = _make_params(workspace=self.workspace, provider=provider)

        async def patched_run_tools(*args, **kwargs):
            return [
                _make_tool_result("toolu_001"),
                _make_hook_stopped_attachment(tool_use_id="toolu_001"),
            ]

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=patched_run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertIsInstance(terminal, Terminal)
        self.assertEqual(terminal.reason, "hook_stopped")

    def test_hook_stopped_attachment_yields_messages_before_terminating(self):
        """The attachment and the tool_result should both be yielded
        before the terminal fires — consumers see the marker first,
        then the loop ends.
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _make_tool_use_response(
            tool_use_id="toolu_002", workspace=self.workspace,
        )

        params = _make_params(workspace=self.workspace, provider=provider)
        attachment = _make_hook_stopped_attachment(tool_use_id="toolu_002")

        async def patched_run_tools(*args, **kwargs):
            return [_make_tool_result("toolu_002"), attachment]

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=patched_run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "hook_stopped")
        # The attachment is yielded as part of tool_results — confirm it
        # made it to the consumer stream.
        attachments_yielded = [
            m for m in messages if isinstance(m, AttachmentMessage)
        ]
        self.assertGreaterEqual(len(attachments_yielded), 1)

    def test_normal_tool_result_does_not_fire_hook_stopped(self):
        """Negative case: regular tool_results transition to next turn;
        when the next turn completes normally the terminal is ``completed``.
        """
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Turn 1: tool_use. Turn 2: end_turn (completion).
        provider.chat.side_effect = [
            _make_tool_use_response(
                tool_use_id="toolu_003", workspace=self.workspace,
            ),
            _make_completion_response(),
        ]

        params = _make_params(workspace=self.workspace, provider=provider)

        async def patched_run_tools(*args, **kwargs):
            return [_make_tool_result("toolu_003")]  # no attachment

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=patched_run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")

    def test_abort_wins_over_hook_stopped(self):
        """Even when both an abort signal and a hook_stopped attachment
        are present, the loop must exit with ``aborted_tools`` — mirrors
        TS where the abort check at query.ts:1665 precedes the
        hook_stopped check at :1698.
        """
        abort = AbortController()
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _make_tool_use_response(
            tool_use_id="toolu_004", workspace=self.workspace,
        )

        params = _make_params(
            workspace=self.workspace, provider=provider, abort=abort,
        )

        async def patched_run_tools(*args, **kwargs):
            # Trip the abort signal AND emit a hook_stopped attachment.
            # The abort check sits between these and the hook_stopped
            # check, so it must win.
            abort.abort("user_abort")
            return [
                _make_tool_result("toolu_004"),
                _make_hook_stopped_attachment(tool_use_id="toolu_004"),
            ]

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=patched_run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "aborted_tools")


class TestHookStoppedDoesNotEmitMaxTurnsAttachment(unittest.TestCase):
    """A hook_stopped exit must NOT also yield ``max_turns_reached``
    even when this turn would have been the final one. Mirrors TS where
    the hook_stopped return at query.ts:1701 precedes the max_turns
    check at :1885.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hook_stopped_at_final_turn_skips_max_turns_attachment(self):
        from src.types.messages import SystemMessage
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = _make_tool_use_response(
            tool_use_id="toolu_005", workspace=self.workspace,
        )

        # max_turns=1: the upcoming next_turn_count would be 2, which
        # would normally trip the max_turns terminal. The hook_stopped
        # scan runs BEFORE that check, so we should get hook_stopped
        # and no max_turns_reached attachment.
        params = _make_params(
            workspace=self.workspace, provider=provider, max_turns=1,
        )

        async def patched_run_tools(*args, **kwargs):
            return [
                _make_tool_result("toolu_005"),
                _make_hook_stopped_attachment(tool_use_id="toolu_005"),
            ]

        with patch(
            "src.query.query._run_tools_partitioned",
            side_effect=patched_run_tools,
        ):
            messages, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "hook_stopped")
        max_turns_attachments = [
            m for m in messages
            if isinstance(m, SystemMessage)
            and getattr(m, "subtype", None) == "max_turns_reached"
        ]
        self.assertEqual(
            max_turns_attachments, [],
            "hook_stopped must not also yield max_turns_reached",
        )


if __name__ == "__main__":
    unittest.main()
