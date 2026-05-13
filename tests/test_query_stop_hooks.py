"""Ch5/C — query() ↔ handle_stop_hooks integration tests.

Verifies the contracts from chapter 5 §"Stop Hooks":
  C.1 — handle_stop_hooks_streaming is called at no-tool-use exit
  C.2 — stop_hook_active suppresses re-firing on the blocking retry
  C.3 — stop hooks are SKIPPED when last message is API error
        (death-spiral guard per chapter §"Death Spiral Guard" point 4)
  C.4 — has_attempted_reactive_compact is preserved across blocking
        retry (chapter §"Death Spiral Guard" point 5)
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
from src.query.stop_hooks import StopHookResult
from src.query.transitions import TerminalHolder


def _run(coro):
    return asyncio.run(coro)


class _StopHooksTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _params(self, provider):
        return QueryParams(
            messages=[UserMessage(content="Do work")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )


class TestStopHooksIntegration(_StopHooksTestBase):
    """C.1 — handle_stop_hooks_streaming is invoked at no-tool-use exit."""

    def test_prevent_continuation_yields_stop_hook_prevented_terminal(self):
        """C.1: when a stop hook returns prevent_continuation=True, the
        loop exits with Terminal(reason='stop_hook_prevented')."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Done.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        async def fake_stop_hooks(**kw):
            result = StopHookResult(prevent_continuation=True)
            # Mimic the streaming generator's contract: yield messages
            # then a final StopHookResult.
            yield result

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            with patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())
        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "stop_hook_prevented")

    def test_blocking_errors_inject_user_messages_and_retry(self):
        """C.1 + C.2: when a stop hook returns blocking errors, the loop
        injects them as user messages, sets stop_hook_active=True, and
        retries the model call. On the retry, the same hook would NOT
        re-fire (stop_hook_active=True passed through). After the retry
        succeeds with no blocking errors, terminal is `completed`."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="First try.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            ChatResponse(
                content="Retry after lint fixed.",
                model="test",
                usage={"input_tokens": 20, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        call_counter = {"count": 0}
        observed_stop_hook_active: list[bool | None] = []

        async def fake_stop_hooks(*, stop_hook_active=None, **kw):
            call_counter["count"] += 1
            observed_stop_hook_active.append(stop_hook_active)
            if call_counter["count"] == 1:
                # First call: emit blocking error
                err = UserMessage(
                    content="Stop hook blocked: linter found 1 issue",
                    isMeta=True,
                )
                yield err
                yield StopHookResult(blocking_errors=[err])
            else:
                # Second call (after retry): clean
                yield StopHookResult()

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            with patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Two model calls (initial + post-blocking retry).
        self.assertEqual(provider.chat.call_count, 2)
        # Two stop-hook calls. The second received stop_hook_active=True.
        self.assertEqual(call_counter["count"], 2)
        self.assertIsNone(observed_stop_hook_active[0])
        self.assertEqual(observed_stop_hook_active[1], True)

    def test_no_blocking_no_prevent_falls_through_to_completed(self):
        """C.1: when stop hooks return empty StopHookResult, the loop
        falls through to Terminal(completed)."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="All good.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        async def fake_stop_hooks(**kw):
            yield StopHookResult()

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            with patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())
        self.assertEqual(holder.value.reason, "completed")


class TestStopHooksDeathSpiralGuards(_StopHooksTestBase):
    """C.3 + C.4 — death-spiral guards (chapter §"Death Spiral Guard")."""

    def test_stop_hooks_skipped_on_api_error(self):
        """C.3: when the last message is an API error (e.g. rate-limit,
        invalid_request), stop hooks must NOT fire. Otherwise a
        blocking hook on an error response would retry-loop forever."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = Exception(
            "API Error 400: invalid_request_error - some unrelated error"
        )

        stop_hook_calls = {"count": 0}

        async def fake_stop_hooks(**kw):
            stop_hook_calls["count"] += 1
            yield StopHookResult()

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            with patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        # The outer error handler catches the exception, yields a model
        # error message, and exits with Terminal(model_error). Stop hooks
        # never fire on this path either way — but the more interesting
        # case is when _call_model_sync returns a tagged API error
        # message. Verify that path too:
        # (The exception in side_effect above is the unhandled-error
        # branch; stop hooks correctly aren't called.)
        self.assertEqual(stop_hook_calls["count"], 0)

    def test_stop_hooks_skipped_on_handled_api_error(self):
        """C.3: when _call_model_sync returns a tagged API error
        (e.g. invalid_request via the exception-handler branch), the
        no-follow-up path detects isApiErrorMessage and exits with
        Terminal(completed) WITHOUT calling stop hooks."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Raise an error that _call_model_sync DOESN'T have a dedicated
        # branch for — it will re-raise, hit the outer exception handler.
        # But we want to test the path where _call_model_sync RETURNS
        # an API-error message instead. The cleanest way: mock the
        # provider to raise a PTL exception. Without reactive_compact
        # enabled, the surfaced PTL is an isApiErrorMessage and the
        # path goes through the "Terminal(completed)" exit AFTER the
        # B.2 branch falls through (because has_attempted=False but
        # reactive_compact_enabled=False → guard never fires).
        # ... Actually, simpler: patch the entire withhold check so a
        # plain API-error message reaches the C.3 guard.
        provider.chat.return_value = ChatResponse(
            content="some response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        stop_hook_calls = {"count": 0}

        async def fake_stop_hooks(**kw):
            stop_hook_calls["count"] += 1
            yield StopHookResult()

        params = self._params(provider)
        holder = TerminalHolder()

        # Inject a fake "API error" assistant message by patching
        # _call_model_sync to return one directly.
        from src.types.messages import AssistantMessage as _AM
        err_msg = _AM(content="rate-limited", isApiErrorMessage=True)
        err_msg._api_error = "rate_limit"

        async def fake_call_model(**kw):
            return [err_msg], []

        async def run():
            with patch(
                "src.query.query._call_model_sync",
                side_effect=fake_call_model,
            ), patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # Stop hooks must NOT have been invoked on the API-error path.
        self.assertEqual(stop_hook_calls["count"], 0)

    def test_blocking_retry_preserves_reactive_compact_guard(self):
        """C.4: when a prior turn ran reactive_compact and the next
        turn's stop hook blocks, the blocking-retry state must KEEP
        has_attempted_reactive_compact=True. Resetting to False would
        re-enable PTL recovery on the retry — chapter §"Death Spiral
        Guard" point 5 documents the infinite-loop failure mode this
        guards against."""
        from src.query.transitions import QueryState

        # Build a fake state where has_attempted_reactive_compact is
        # already True (as if a prior turn ran reactive_compact). Then
        # have the stop hook block. The new state should preserve the
        # flag.
        #
        # We assert this structurally: by patching handle_stop_hooks to
        # return blocking errors AND watching the model-call count.
        # If has_attempted gets reset, a subsequent PTL would trigger
        # another reactive_compact. We engineer the scenario so that
        # WOULD happen if the guard didn't hold.
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # First call: succeed normally. Second call (after blocking
        # retry): raise PTL — if the guard fails, reactive_compact
        # would run a second time.
        provider.chat.side_effect = [
            ChatResponse(
                content="First reply.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            Exception("Prompt is too long"),
        ]

        from src.services.compact.reactive_compact import ReactiveCompactResult

        compact_calls = []

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            compact_calls.append(1)
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="[summary]")],
                tokens_before=10_000,
                tokens_after=5_000,
            )

        hook_call = {"count": 0}

        async def fake_stop_hooks(**kw):
            hook_call["count"] += 1
            if hook_call["count"] == 1:
                err = UserMessage(content="block", isMeta=True)
                yield err
                yield StopHookResult(blocking_errors=[err])
            else:
                yield StopHookResult()

        # Pre-load state to indicate reactive_compact already ran this
        # session. We do that by simulating a prior successful
        # reactive_compact via a setup: monkeypatch the loop to start
        # with has_attempted_reactive_compact=True. Without an injection
        # hook, the easiest path is to set up an initial state where
        # we trigger reactive_compact before the stop-hook blocking
        # path. So:
        #   Turn 1: provider returns OK → stop hook BLOCKS → retry
        #   (has_attempted_reactive_compact was False, stays False)
        #   Turn 2 (retry): provider raises PTL → reactive_compact fires
        # This isn't what we want. To properly test C.4, we need
        # has_attempted_reactive_compact=True BEFORE the stop-hook
        # blocks. The easiest way is to have:
        #   Turn 1: PTL → reactive_compact → compacted=True
        #   Turn 2 (post-compact): clean response → stop hook BLOCKS
        #   Turn 3 (post-stop-retry): another PTL → would re-fire
        #         reactive_compact IF the guard reset
        # That's the test path. Re-engineer side_effects:
        provider.chat.side_effect = [
            Exception("Prompt is too long"),  # turn 1: triggers compact
            ChatResponse(  # turn 2 (post-compact): clean response
                content="After compact.",
                model="test",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="end_turn",
                tool_uses=None,
            ),
            Exception("Prompt is too long"),  # turn 3 (post-stop-retry): PTL again
        ]

        params = self._params(provider)
        holder = TerminalHolder()

        async def run():
            with patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ), patch(
                "src.query.stop_hooks.handle_stop_hooks_streaming",
                side_effect=fake_stop_hooks,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        # The critical assertion: reactive_compact was called EXACTLY
        # ONCE. If the C.4 guard had failed and the blocking-retry
        # state had reset has_attempted_reactive_compact=False, the
        # turn-3 PTL would have triggered a second reactive_compact
        # attempt. The guard preservation prevents this.
        self.assertEqual(
            len(compact_calls), 1,
            "C.4 guard failure: reactive_compact must not re-fire "
            "after stop-hook blocking retry",
        )
        # Terminal should be prompt_too_long (the surfaced post-compact
        # PTL after the guard prevented another compaction attempt).
        self.assertEqual(holder.value.reason, "prompt_too_long")


if __name__ == "__main__":
    unittest.main()
