import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.query import (
    ESCALATED_MAX_TOKENS,
    QueryParams,
    StreamEvent,
    query,
)


def _run(coro):
    return asyncio.run(coro)


class TestMaxOutputTokensEscalation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_escalation_to_64k(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated = ChatResponse(
            content="Partial output...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output with more content.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5000},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated, full]

        messages = [UserMessage(content="Write a long story")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        self.assertEqual(provider.chat.call_count, 2)

        second_call = provider.chat.call_args_list[1]
        self.assertEqual(second_call[1].get("max_tokens"), ESCALATED_MAX_TOKENS)

    def test_recovery_with_resume_message(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated_with_override = ChatResponse(
            content="Partial output again...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output.",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5000},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated_with_override, full]

        messages = [UserMessage(content="Write a long story")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
            max_output_tokens_override=ESCALATED_MAX_TOKENS,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        self.assertEqual(provider.chat.call_count, 2)


class TestRecoveryExhaustion(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recovery_stops_after_max_attempts(self):
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        truncated = ChatResponse(
            content="Partial...",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 8000},
            finish_reason="max_tokens",
            tool_uses=None,
        )
        provider.chat.return_value = truncated

        messages = [UserMessage(content="Write a very long story")]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=20,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        self.assertLessEqual(provider.chat.call_count, 6)


class TestPhaseBPromptTooLongRecovery(unittest.TestCase):
    """Ch5/B.1+B.2 — withholding + reactive_compact recovery for PTL."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _build_params(self, provider):
        from src.query.transitions import TerminalHolder
        params = QueryParams(
            messages=[UserMessage(content="Long task")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        return params, TerminalHolder()

    def test_ptl_message_withheld_from_stream(self):
        """B.1: PTL error tagged in _call_model_sync should NOT yield
        through to the consumer; recovery (B.2) replaces it."""
        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        # First call: simulate PTL error from the API
        provider.chat.side_effect = [
            Exception("Prompt is too long: 250000 tokens > 200000"),
            ChatResponse(
                content="Recovered output",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 50},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        # Mock reactive_compact to return success (so the recovery path
        # fires and the loop continues to the second model call).
        from src.services.compact.reactive_compact import ReactiveCompactResult

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="[summary]")],
                tokens_before=250_000,
                tokens_after=10_000,
            )

        params, holder = self._build_params(provider)
        collected = []

        async def run():
            from src.query.query import query
            with unittest.mock.patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ):
                async for msg in query(params, terminal_holder=holder):
                    collected.append(msg)

        _run(run())

        # No assistant message in the stream should carry the PTL error tag —
        # the withheld message was suppressed and replaced by the recovery
        # output.
        ptl_messages = [
            m for m in collected
            if isinstance(m, AssistantMessage)
            and getattr(m, "_api_error", None) == "prompt_too_long"
        ]
        self.assertEqual(
            ptl_messages, [],
            "PTL message must be withheld from stream during recovery",
        )

    def test_ptl_triggers_reactive_compact_and_terminal_completed(self):
        """B.2: when reactive_compact succeeds, the loop continues and
        terminates as `completed` (not `prompt_too_long`)."""
        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            Exception("Prompt is too long"),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 20},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        from src.services.compact.reactive_compact import ReactiveCompactResult

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="[summary]")],
                tokens_before=250_000,
                tokens_after=5_000,
            )

        params, holder = self._build_params(provider)

        async def run():
            from src.query.query import query
            with unittest.mock.patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        self.assertIsNotNone(holder.value, "Terminal must be set")
        self.assertEqual(holder.value.reason, "completed")
        self.assertEqual(provider.chat.call_count, 2)

    def test_ptl_compact_failure_surfaces_terminal(self):
        """B.2: when reactive_compact returns compacted=False, the loop
        surfaces the PTL message and exits with terminal `prompt_too_long`.
        (Single-iteration exit; covers the no-recovery-available path.)"""
        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = lambda *a, **kw: (_ for _ in ()).throw(
            Exception("Prompt is too long")
        )

        from src.services.compact.reactive_compact import ReactiveCompactResult

        compact_calls = []

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            compact_calls.append(1)
            return ReactiveCompactResult(
                compacted=False,
                messages=list(messages),
                tokens_before=250_000,
                error="Failed to reduce context",
            )

        params, holder = self._build_params(provider)
        collected = []

        async def run():
            from src.query.query import query
            with unittest.mock.patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ):
                async for msg in query(params, terminal_holder=holder):
                    collected.append(msg)

        _run(run())

        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "prompt_too_long")
        self.assertEqual(len(compact_calls), 1)
        # Last assistant message must be the surfaced PTL error.
        ptl = [m for m in collected if isinstance(m, AssistantMessage)
               and getattr(m, "_api_error", None) == "prompt_too_long"]
        self.assertEqual(len(ptl), 1)

    def test_ptl_one_shot_guard_post_compact_does_not_retry(self):
        """B.2 ONE-SHOT GUARD (post-critic-strengthening): reactive_compact
        succeeds first; the post-compact retry then ALSO raises PTL; the
        guard (``has_attempted_reactive_compact=True`` carried in
        ``QueryState``) prevents a second reactive_compact attempt; the
        terminal is `prompt_too_long` and the second PTL message IS
        surfaced (first one was withheld during the recovery attempt)."""
        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        # Both calls raise PTL — first triggers reactive_compact (which
        # succeeds), second still raises after compaction.
        provider.chat.side_effect = lambda *a, **kw: (_ for _ in ()).throw(
            Exception("Prompt is too long")
        )

        from src.services.compact.reactive_compact import ReactiveCompactResult

        compact_calls = []

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            compact_calls.append(1)
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="[summary]")],
                tokens_before=250_000,
                tokens_after=10_000,
            )

        params, holder = self._build_params(provider)

        async def run():
            from src.query.query import query
            with unittest.mock.patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "prompt_too_long")
        # One-shot guard: reactive_compact called EXACTLY ONCE even though
        # the second model call ALSO raised PTL. Without the guard, the
        # loop would attempt reactive_compact a second time and burn API
        # budget in the death-spiral pattern documented in chapter §"Death
        # Spiral Guard" point 1.
        self.assertEqual(
            len(compact_calls), 1,
            "has_attempted_reactive_compact one-shot guard must prevent "
            "a second reactive_compact attempt within the same loop turn",
        )
        # Two model calls: first raised PTL (triggered recovery), second
        # raised PTL (post-recovery, surfaced as Terminal).
        self.assertEqual(provider.chat.call_count, 2)

    def test_media_size_message_withheld_and_recovers(self):
        """B.1: media-size errors are tagged and withheld;
        B.2: recovery via reactive_compact succeeds, terminal `completed`."""
        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            Exception("image exceeds the maximum allowed dimensions"),
            ChatResponse(
                content="Done.",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 20},
                finish_reason="end_turn",
                tool_uses=None,
            ),
        ]

        from src.services.compact.reactive_compact import ReactiveCompactResult

        async def fake_reactive_compact(messages, error, provider, model, **kw):
            return ReactiveCompactResult(
                compacted=True,
                messages=[UserMessage(content="[summary]")],
                tokens_before=10_000,
                tokens_after=5_000,
            )

        params, holder = self._build_params(provider)
        collected = []

        async def run():
            from src.query.query import query
            with unittest.mock.patch(
                "src.services.compact.reactive_compact.reactive_compact",
                side_effect=fake_reactive_compact,
            ):
                async for msg in query(params, terminal_holder=holder):
                    collected.append(msg)

        _run(run())

        media_msgs = [
            m for m in collected
            if isinstance(m, AssistantMessage)
            and getattr(m, "_api_error", None) == "media_size"
        ]
        self.assertEqual(media_msgs, [], "media-size message must be withheld")
        self.assertEqual(holder.value.reason, "completed")


class TestImageUnsupportedClassification(unittest.TestCase):
    """Image-unsupported errors must be classified at _call_model_sync so
    the engine can strip image history (instead of bubbling through the
    generic catch-all that loses the tag)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_openrouter_404_yields_tagged_api_error(self):
        """OpenRouter's "No endpoints found that support image input"
        must surface as a tagged ``_api_error == "image_unsupported"``
        AssistantMessage — NOT a generic ``isApiErrorMessage`` with no
        tag. The tag is what triggers the engine's strip-and-recover
        path, so dropping it would re-introduce the context-stuck bug."""
        from unittest.mock import MagicMock
        from src.types.content_blocks import ImageBlock, TextBlock
        from src.services.api.errors import IMAGE_UNSUPPORTED_ERROR_MESSAGE

        provider = MagicMock()
        # _call_model_sync prefers chat_stream_response; falling back to
        # chat only on NotImplementedError. Raise the 404 from both so
        # we don't depend on the streaming/sync code path.
        err = Exception(
            "Error code: 404 - {'error': {'message': "
            "'No endpoints found that support image input', 'code': 404}}"
        )
        provider.chat_stream_response.side_effect = err
        provider.chat.side_effect = err

        messages = [
            UserMessage(content=[
                TextBlock(text="describe"),
                ImageBlock(source={
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "AAAA",
                }),
            ])
        ]
        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=2,
        )

        collected = []

        async def run():
            async for msg in query(params):
                collected.append(msg)

        _run(run())

        tagged = [
            m for m in collected
            if isinstance(m, AssistantMessage)
            and getattr(m, "_api_error", None) == "image_unsupported"
        ]
        self.assertEqual(
            len(tagged), 1,
            "exactly one image_unsupported-tagged AssistantMessage must reach the consumer",
        )
        self.assertTrue(tagged[0].isApiErrorMessage)
        # Message must be the user-friendly constant, not the raw 404.
        self.assertEqual(tagged[0].content, IMAGE_UNSUPPORTED_ERROR_MESSAGE)
        # errorDetails must preserve the raw provider payload — a
        # future bug-reporter ("the fix didn't work for me") needs the
        # actual 404 text to diagnose, not just the friendly message.
        self.assertIsNotNone(tagged[0].errorDetails)
        self.assertIn(
            "No endpoints found that support image input",
            tagged[0].errorDetails or "",
        )


class TestPhaseBBlockingLimitPreemption(unittest.TestCase):
    """Ch5/B.4 + B.5 — pre-emption guards before the API call."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_blocking_limit_preemption(self):
        """B.4: when context is past blocking limit AND no recovery is
        available (autocompact off), yield blocking_limit terminal
        without calling the provider."""
        import os
        from unittest.mock import MagicMock, patch
        from src.query.transitions import TerminalHolder

        provider = MagicMock()
        provider.context_window = 10_000  # tiny window

        # With cw=10_000 the effective window floors at 33_000 (reserved
        # 20k + AUTOCOMPACT_BUFFER 13k), so blocking_limit ~= 30_000.
        # "x " * 200_000 yields ~50k estimated tokens, comfortably over.
        big_text = "x " * 200_000  # ~50k tokens
        messages = [UserMessage(content=big_text)]

        params = QueryParams(
            messages=messages,
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
            from src.query.query import query
            with patch.dict(os.environ, {"DISABLE_AUTO_COMPACT": "1"}):
                async for _ in query(params, terminal_holder=holder):
                    pass

        _run(run())

        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "blocking_limit")
        # Provider was never called (this is the whole point of the guard).
        provider.chat.assert_not_called()
        provider.chat_stream_response.assert_not_called()

    def test_autocompact_circuit_breaker_returns_blocking_limit(self):
        """B.5: when autocompact has failed 3 times AND we're still
        above the threshold, return blocking_limit cleanly (vs.
        burning another 500)."""
        import os
        from unittest.mock import MagicMock, patch
        from src.query.transitions import TerminalHolder
        from src.services.compact.autocompact import AutoCompactTracking
        from src.services.compact.pipeline import PipelineConfig

        provider = MagicMock()
        provider.context_window = 100_000
        # If the guard fires, the provider is never called. We add a
        # canned response just in case the guard misses, so the test
        # fails loudly with a different message.
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Should not be reached",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        big_text = "y " * 200_000  # ~100k tokens, well above autocompact threshold
        messages = [UserMessage(content=big_text)]

        tracking = AutoCompactTracking(consecutive_failures=3)

        # Pipeline config carries the tripped tracking. The pipeline
        # itself will short-circuit autocompact (since failures>=3),
        # so the breaker stays tripped and the B.5 guard fires.
        pipeline_config = PipelineConfig(
            provider=provider,
            model="test",
            autocompact_tracking=tracking,
        )

        params = QueryParams(
            messages=messages,
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
            pipeline_config=pipeline_config,
        )
        holder = TerminalHolder()

        collected = []

        async def run():
            from src.query.query import query
            async for msg in query(params, terminal_holder=holder):
                collected.append(msg)

        _run(run())

        self.assertIsNotNone(holder.value)
        self.assertEqual(holder.value.reason, "blocking_limit")
        # Verify the user-visible message mentions automatic compaction.
        msgs = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertTrue(
            any("automatic compaction" in str(getattr(m, "content", ""))
                for m in msgs),
            f"Expected 'automatic compaction' in surfaced message, got {msgs}",
        )


if __name__ == "__main__":
    unittest.main()
