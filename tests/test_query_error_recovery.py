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
    run_query,
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


class TestReactiveRecoveryActuallyRuns(unittest.TestCase):
    """The recovery lane must RETRY, not just relabel the terminal.

    Both lanes were dead. ``query.py`` builds its trigger as
    ``PromptTooLongError("withheld during streaming, recovering")`` because
    the original provider exception is consumed during streaming and only the
    classification needs to survive — but the gate
    (``reactive_compact.is_prompt_too_long_error``) was a pure SUBSTRING test
    for "prompt is too long" / "prompt_too_long" / "context_length_exceeded".
    A typed ``PromptTooLongError`` matched none of them, so
    ``reactive_compact`` returned ``compacted=False`` on its first line and
    nothing downstream ran: no compaction, no image strip, no retry.

    Measured on main before the fix: ONE provider call for both a
    prompt-too-long and a media rejection.

    The lane's existing tests all stub ``reactive_compact`` itself
    (``fake_reactive_compact`` returning ``compacted=True``), so the gate was
    never exercised — which is how it stayed broken from 2026-05 to 2026-08.
    These tests deliberately do NOT stub it: they count provider calls.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.abort = AbortController()

    def tearDown(self):
        self.tmp.cleanup()

    def _drive(self, error_text, messages):
        from unittest.mock import MagicMock

        calls = {"n": 0, "images": []}

        def side(msgs, *a, **k):
            calls["n"] += 1
            n = 0
            for m in msgs:
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if isinstance(c, list):
                    for b in c:
                        t = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                        if t in ("image", "image_url", "document"):
                            n += 1
            calls["images"].append(n)
            raise Exception(error_text)

        provider = MagicMock()
        provider.chat_stream_response.side_effect = side
        provider.chat.side_effect = side
        params = QueryParams(
            messages=messages,
            system_prompt="s",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=ToolContext(workspace_root=self.workspace),
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        _msgs, terminal = _run(run_query(params))
        return terminal, calls

    @staticmethod
    def _image_convo(n):
        from src.types.content_blocks import ImageBlock, TextBlock

        ms = [UserMessage(content=[TextBlock(text="analyse these frames")])]
        for i in range(n):
            ms.append(UserMessage(content=[
                TextBlock(text=f"frame {i}"),
                ImageBlock(source={
                    "type": "base64", "media_type": "image/jpeg", "data": "x" * 300,
                }),
            ]))
        return ms

    def test_media_rejection_strips_images_and_retries(self):
        """THE regression guard: a retry must happen AND carry fewer images."""
        terminal, calls = self._drive(
            "Exceeded maximum number of images (50) allowed in the request.",
            self._image_convo(60),
        )
        self.assertGreater(
            calls["n"], 1,
            "no retry happened — the recovery lane is dead again",
        )
        self.assertGreater(calls["images"][0], 0, "first attempt should carry images")
        self.assertLess(
            calls["images"][1], calls["images"][0],
            f"the retry must carry FEWER images; got {calls['images']}",
        )
        self.assertEqual(terminal.reason, "image_error")

    def test_prompt_too_long_recovery_retries(self):
        """The pre-existing lane, dead by the identical bug since 2026-05."""
        terminal, calls = self._drive(
            "prompt is too long: 137500 tokens > 135000 maximum",
            self._image_convo(40),
        )
        self.assertGreater(
            calls["n"], 1,
            "prompt_too_long recovery never retried — the gate is broken again",
        )
        self.assertEqual(terminal.reason, "prompt_too_long")

    def test_the_synthetic_trigger_passes_its_own_gate(self):
        """Pins the exact mismatch, so a future edit to either side is caught.

        ``query.py`` constructs this error; ``reactive_compact`` gates on it.
        The two live in different modules and drifted apart silently.
        """
        from src.services.api.errors import PromptTooLongError
        from src.services.compact.reactive_compact import is_prompt_too_long_error

        self.assertTrue(
            is_prompt_too_long_error(
                PromptTooLongError("withheld during streaming, recovering")
            ),
            "a typed PromptTooLongError must satisfy the PromptTooLong gate "
            "regardless of its message",
        )
        # ...and the string arm still works for untyped provider exceptions.
        self.assertTrue(is_prompt_too_long_error(Exception("prompt is too long")))
        self.assertFalse(is_prompt_too_long_error(Exception("network error")))

    def test_retryable_errors_mentioning_images_stay_retryable(self):
        """A 429/5xx whose body mentions images must NOT become a media
        terminal — that would take it out of the retry lane."""
        from src.services.api.errors import is_media_size_error

        self.assertFalse(
            is_media_size_error("Rate limit reached for images: too many images generated")
        )


class TestMediaRecoveryWhenTheSummarizerIsDown(unittest.TestCase):
    """The media path must not depend on a working summarizer.

    ``reactive_compact``'s emergency fallback drops the OLDEST messages and
    accepts the result on a TOKEN test (``tokens_after < tokens_before *
    0.7``). Image count is never consulted. For the case that motivates this
    path — an agent reading frames in a loop, so the images sit in the RECENT
    tail — dropping old text satisfies that test while leaving the images in
    place. Measured directly against the compactor: 200 messages / 60 images
    -> 40 messages / 40 images, returned as ``compacted=True``.

    So routing media through the token compactor can "succeed" and still
    retry over the cap, burning the one-shot flag. Stripping is deterministic.

    This test forces the summarizer to fail so the two paths diverge — with a
    working summarizer both happen to reach zero images, which is why a
    simpler test cannot tell them apart.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.abort = AbortController()

    def tearDown(self):
        self.tmp.cleanup()

    def test_images_still_dropped_without_a_summarizer(self):
        from unittest import mock
        from unittest.mock import MagicMock

        from src.types.content_blocks import ImageBlock, TextBlock

        # Text-heavy head, images concentrated in the recent tail.
        msgs = []
        for i in range(60):
            msgs.append(UserMessage(content=[TextBlock(text=f"step {i} " + "lorem " * 80)]))
        for i in range(40):
            msgs.append(UserMessage(content=[
                TextBlock(text=f"frame {i}"),
                ImageBlock(source={
                    "type": "base64", "media_type": "image/jpeg", "data": "x" * 300,
                }),
            ]))

        seen = []

        def side(api_msgs, *a, **k):
            n = 0
            for m in api_msgs:
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if isinstance(c, list):
                    for b in c:
                        t = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                        if t in ("image", "image_url", "document"):
                            n += 1
            seen.append(n)
            raise Exception("Exceeded maximum number of images (50) allowed in the request.")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = side
        provider.chat.side_effect = side

        async def _summarizer_down(ctx):
            raise RuntimeError("summarizer unavailable")

        params = QueryParams(
            messages=msgs,
            system_prompt="s",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=ToolContext(workspace_root=self.workspace),
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )
        with mock.patch(
            "src.services.compact.reactive_compact.compact_conversation",
            _summarizer_down,
        ):
            _run(run_query(params))

        self.assertGreater(len(seen), 1, "no retry happened")
        self.assertGreater(seen[0], 0, "first attempt should carry images")
        self.assertEqual(
            seen[1], 0,
            "the retry must carry NO images even with the summarizer down; "
            f"got {seen} — the token-shaped compactor leaves images behind",
        )
