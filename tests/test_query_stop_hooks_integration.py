"""Phase C acceptance tests: stop hooks fire from query() at no-tool-use exit.

Covers:
- C.1: handle_stop_hooks_streaming is invoked when needs_follow_up=False
- C.1: prevent_continuation → Terminal(reason="stop_hook_prevented")
- C.1: blocking_errors → loop continues with stop_hook_active=True
- C.2: stop_hook_active passed on retry
- C.3: skip stop hooks when last message is API error (twice: PTL path + generic)
- C.4: preserve has_attempted_reactive_compact across stop-hook retry
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import QueryParams, run_query
from src.query.transitions import Terminal
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController


def _run(coro):
    return asyncio.run(coro)


def _make_params(workspace: Path, provider: MagicMock, max_turns: int = 10) -> QueryParams:
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


class TestStopHookPreventContinuation(unittest.TestCase):
    """C.1: prevent_continuation hooks return Terminal(stop_hook_prevented)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prevent_continuation_returns_stop_hook_prevented(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="I'm done.",
            model="test",
            usage={"input_tokens": 5, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        from src.query.stop_hooks import StopHookResult

        async def fake_streaming(*args, **kwargs):
            yield StopHookResult(blocking_errors=[], prevent_continuation=True)

        params = _make_params(self.workspace, provider)
        with patch(
            "src.query.stop_hooks.handle_stop_hooks_streaming",
            side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
        ):
            _, terminal = _run(run_query(params))
        self.assertEqual(terminal.reason, "stop_hook_prevented")


class TestStopHookBlockingErrors(unittest.TestCase):
    """C.1: blocking_errors trigger a retry with stop_hook_active=True."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocking_errors_retry_then_complete(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I'm done.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="Fixed the lint issue.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        from src.query.stop_hooks import StopHookResult
        call_count = {"n": 0}

        async def fake_streaming(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                err_msg = UserMessage(content="Lint says fix line 42", isMeta=True)
                yield StopHookResult(
                    blocking_errors=[err_msg],
                    prevent_continuation=False,
                )
            else:
                yield StopHookResult(blocking_errors=[], prevent_continuation=False)

        params = _make_params(self.workspace, provider)
        with patch(
            "src.query.stop_hooks.handle_stop_hooks_streaming",
            side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
        ):
            _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)
        self.assertEqual(call_count["n"], 2)


class TestStopHookActiveFlag(unittest.TestCase):
    """C.2: stop_hook_active=True passed on retry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stop_hook_active_set_on_retry(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="Done again.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        from src.query.stop_hooks import StopHookResult
        call_count = {"n": 0}
        observed_flags: list[bool | None] = []

        async def fake_streaming(*args, **kwargs):
            call_count["n"] += 1
            observed_flags.append(kwargs.get("stop_hook_active"))
            if call_count["n"] == 1:
                err = UserMessage(content="blocked", isMeta=True)
                yield StopHookResult(
                    blocking_errors=[err],
                    prevent_continuation=False,
                )
            else:
                yield StopHookResult(blocking_errors=[], prevent_continuation=False)

        params = _make_params(self.workspace, provider)
        with patch(
            "src.query.stop_hooks.handle_stop_hooks_streaming",
            side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
        ):
            _run(run_query(params))

        self.assertEqual(len(observed_flags), 2)
        self.assertIn(observed_flags[0], (None, False))
        self.assertTrue(observed_flags[1])


class TestStopHookSkippedOnApiError(unittest.TestCase):
    """C.3: when last message is API error (PTL exhaustion path),
    stop hooks must NOT fire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_api_error_skips_stop_hooks(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = RuntimeError("Prompt is too long.")

        from src.services.compact.reactive_compact import ReactiveCompactResult

        async def fail_rc(**kwargs):
            return ReactiveCompactResult(
                compacted=False,
                messages=kwargs["messages"],
                tokens_before=1000,
                error="mocked",
            )

        invoked = {"n": 0}

        async def fake_streaming(*args, **kwargs):
            invoked["n"] += 1
            from src.query.stop_hooks import StopHookResult
            yield StopHookResult(blocking_errors=[], prevent_continuation=False)

        params = _make_params(self.workspace, provider)
        with (
            patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fail_rc,
            ),
            patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
            ),
        ):
            _, terminal = _run(run_query(params))

        self.assertEqual(terminal.reason, "prompt_too_long")
        self.assertEqual(invoked["n"], 0)


class TestStopHookPreservesReactiveCompactFlag(unittest.TestCase):
    """C.4: has_attempted_reactive_compact is NOT reset on blocking-error retry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reactive_compact_flag_preserved_across_stop_hook_retry(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            RuntimeError("Prompt is too long."),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 5, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            RuntimeError("Prompt is too long."),
        ]

        from src.services.compact.reactive_compact import ReactiveCompactResult

        async def succeed_rc(**kwargs):
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="summarized")],
                tokens_before=1000,
                tokens_after=200,
            )

        hook_calls = {"n": 0}

        async def fake_streaming(*args, **kwargs):
            from src.query.stop_hooks import StopHookResult
            hook_calls["n"] += 1
            if hook_calls["n"] == 1:
                yield StopHookResult(
                    blocking_errors=[UserMessage(content="lint", isMeta=True)],
                    prevent_continuation=False,
                )
            else:
                yield StopHookResult(blocking_errors=[], prevent_continuation=False)

        rc_mock = MagicMock(side_effect=succeed_rc)

        params = _make_params(self.workspace, provider)
        with (
            patch(
                "src.services.compact.reactive_compact.reactive_compact",
                new=rc_mock,
            ),
            patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
            ),
        ):
            _, terminal = _run(run_query(params))

        # Turn 3's PTL surfaces as prompt_too_long (no re-attempted
        # reactive_compact). reactive_compact was called exactly ONCE.
        self.assertEqual(rc_mock.call_count, 1)
        self.assertEqual(terminal.reason, "prompt_too_long")


class TestStopHookSkippedOnGenericApiError(unittest.TestCase):
    """C.3 part 2: when last_message is an API error for ANY reason
    (not just PTL exhaustion), stop hooks must NOT fire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_invalid_request_api_error_skips_stop_hooks(self):
        # Synthesize an AssistantMessage flagged isApiErrorMessage=True
        # via a fake provider whose `chat` returns a response that
        # makes _call_model_sync emit such a message. Easiest path:
        # raise a generic exception, which the outer handler turns into
        # an api-error AssistantMessage and exits as model_error.
        #
        # The model_error path actually exits BEFORE reaching the
        # no-tool-use branch — so we instead simulate a different path
        # by mocking the loop to produce an empty-content assistant
        # message that's marked isApiErrorMessage=True via a custom
        # provider-side path. Simplest: trip the PTL recovery
        # exhaustion (which yields the withheld error). That's what
        # TestStopHookSkippedOnApiError above already covers. This
        # test pins a parallel path: when the final message has
        # isApiErrorMessage=True from a non-PTL cause.
        #
        # We patch _call_model_sync directly to return such a message.
        from src.types.content_blocks import TextBlock

        async def fake_api_error_call(**kwargs):
            err = AssistantMessage(
                content=[TextBlock(text="API rejected the request.")],
                isApiErrorMessage=True,
            )
            err._api_error = "invalid_request"  # type: ignore[attr-defined]
            return [err], []

        invoked = {"n": 0}

        async def fake_streaming(*args, **kwargs):
            from src.query.stop_hooks import StopHookResult
            invoked["n"] += 1
            yield StopHookResult(blocking_errors=[], prevent_continuation=False)

        provider = MagicMock()
        provider.chat_stream_response.side_effect = AssertionError(
            "real provider must not be called",
        )

        params = _make_params(self.workspace, provider)

        with (
            patch(
                "src.query.query._call_model_sync",
                side_effect=fake_api_error_call,
            ),
            patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=lambda *a, **kw: fake_streaming(*a, **kw),
            ),
        ):
            _, terminal = _run(run_query(params))

        # Terminal is "completed" per chapter §"Death Spiral Guard"
        # (API error → skip stop hooks → return completed). The
        # important assertion is that the hook never fired.
        self.assertEqual(terminal.reason, "completed")
        self.assertEqual(invoked["n"], 0)


if __name__ == "__main__":
    unittest.main()
