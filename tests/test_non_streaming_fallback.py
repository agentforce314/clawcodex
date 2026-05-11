"""Tests for the non-streaming fallback path in ``call_model`` (Phase D)."""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace

from src.services.api.claude import (
    CallModelOptions,
    ContentBlockStop,
    ErrorEvent,
    FallbackEvent,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageEvent,
    _events_from_non_streaming_response,
    call_model,
)


def _usage(input_tokens: int = 1, output_tokens: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id: str, name: str, input_dict: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input_dict)


class _NonStreamMessages:
    """messages stub where .create(stream=True) raises and stream=False works."""

    def __init__(self, fallback_response: SimpleNamespace) -> None:
        self.fallback_response = fallback_response
        self.last_kwargs: dict | None = None
        self.stream_call_count = 0
        self.nonstream_call_count = 0

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if kwargs.get("stream"):
            self.stream_call_count += 1
            raise ConnectionError("stream creation failed")
        self.nonstream_call_count += 1
        return self.fallback_response


class _FakeClient:
    def __init__(self, messages_impl) -> None:
        self.base_url = ""
        self.messages = messages_impl


class TestEventsFromNonStreamingResponse(unittest.TestCase):
    def test_text_only_response(self) -> None:
        response = SimpleNamespace(
            usage=_usage(input_tokens=10, output_tokens=20),
            model="claude-haiku-4-5",
            content=[_text_block("Hello, world.")],
            stop_reason="end_turn",
        )
        events = _events_from_non_streaming_response(response, "fallback-model")

        # Expected sequence: message_start, text_delta, content_block_stop,
        # message_delta(end_turn), message_stop.
        self.assertEqual(len(events), 5)
        self.assertIsInstance(events[0], MessageStart)
        self.assertEqual(events[0].usage.input_tokens, 10)
        self.assertIsInstance(events[1], TextDelta)
        self.assertEqual(events[1].text, "Hello, world.")
        self.assertIsInstance(events[2], ContentBlockStop)
        self.assertIsInstance(events[3], MessageDelta)
        self.assertEqual(events[3].stop_reason, "end_turn")
        self.assertIsInstance(events[4], MessageStop)

    def test_tool_use_response(self) -> None:
        response = SimpleNamespace(
            usage=_usage(),
            model="claude-haiku-4-5",
            content=[_tool_use_block("tool_1", "Read", {"path": "/x"})],
            stop_reason="tool_use",
        )
        events = _events_from_non_streaming_response(response, "haiku")
        # message_start, tool_use_start, tool_use_delta (json), block_stop,
        # message_delta, message_stop = 6 events
        self.assertEqual(len(events), 6)
        self.assertIsInstance(events[1], ToolUseStart)
        self.assertEqual(events[1].name, "Read")
        self.assertIsInstance(events[2], ToolUseDelta)
        # partial_json contains the serialized input
        self.assertIn("path", events[2].partial_json)
        self.assertIn("/x", events[2].partial_json)

    def test_empty_content_response(self) -> None:
        response = SimpleNamespace(
            usage=_usage(input_tokens=5, output_tokens=0),
            model="claude-haiku-4-5",
            content=[],
            stop_reason="end_turn",
        )
        events = _events_from_non_streaming_response(response, "fallback")
        # Just message_start, message_delta, message_stop
        self.assertEqual(len(events), 3)


class TestCallModelFallback(unittest.IsolatedAsyncioTestCase):
    async def test_stream_creation_failure_triggers_fallback(self) -> None:
        response = SimpleNamespace(
            usage=_usage(),
            model="claude-haiku-4-5",
            content=[_text_block("recovered")],
            stop_reason="end_turn",
        )
        messages = _NonStreamMessages(response)
        client = _FakeClient(messages)

        events: list = []
        async for ev in call_model([], CallModelOptions(model="claude-haiku-4-5"), client=client):
            events.append(ev)

        # First non-fallback event should be a FallbackEvent.
        fallback_events = [e for e in events if isinstance(e, FallbackEvent)]
        self.assertEqual(len(fallback_events), 1)
        self.assertEqual(fallback_events[0].cause, "stream_error")

        # We should have re-shaped the response into stream events.
        text_events = [e for e in events if isinstance(e, TextDelta)]
        self.assertEqual(len(text_events), 1)
        self.assertEqual(text_events[0].text, "recovered")

        # The fallback call had to drop stream=True.
        self.assertEqual(messages.stream_call_count, 1)
        self.assertEqual(messages.nonstream_call_count, 1)
        self.assertFalse(messages.last_kwargs.get("stream", False))

        # The final usage event should be present.
        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        self.assertEqual(len(usage_events), 1)
        self.assertGreater(usage_events[0].usage.output_tokens, 0)

    async def test_fallback_disabled_via_env_var(self) -> None:
        response = SimpleNamespace(
            usage=_usage(), model="claude-haiku-4-5",
            content=[_text_block("would have recovered")], stop_reason="end_turn",
        )
        messages = _NonStreamMessages(response)
        client = _FakeClient(messages)

        os.environ["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
        try:
            events: list = []
            async for ev in call_model(
                [], CallModelOptions(model="claude-haiku-4-5"), client=client,
            ):
                events.append(ev)
        finally:
            os.environ.pop("CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK", None)

        # When fallback is disabled, no FallbackEvent and no recovery — just
        # an ErrorEvent surfacing the original failure.
        self.assertFalse(any(isinstance(e, FallbackEvent) for e in events))
        self.assertFalse(any(isinstance(e, TextDelta) for e in events))
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        self.assertEqual(len(error_events), 1)
        self.assertIn("stream creation failed", error_events[0].error)
        # Fallback path was NOT called.
        self.assertEqual(messages.nonstream_call_count, 0)

    async def test_partial_stream_then_fallback_does_not_double_count_usage(self) -> None:
        """Critical regression test (critic-flagged): when the stream raises
        AFTER emitting message_start, the partial-stream's input_tokens were
        previously summed with the fallback response's input_tokens. Both
        figures represent the SAME request's input cost, so summing them
        inflates totals by 2×. The fallback path must reset
        ``accumulated_usage`` before replaying the response.
        """

        class _PartialStream:
            """Yields one message_start event, then raises on the second pull."""

            def __init__(self) -> None:
                self._yielded_start = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._yielded_start:
                    self._yielded_start = True
                    return SimpleNamespace(
                        type="message_start",
                        message=SimpleNamespace(
                            usage=SimpleNamespace(
                                input_tokens=100,
                                output_tokens=0,
                                cache_creation_input_tokens=50,
                                cache_read_input_tokens=25,
                            ),
                            model="claude-haiku-4-5",
                        ),
                    )
                raise ConnectionError("stream torn down mid-response")

        class _PartialThenFallbackMessages:
            def __init__(self) -> None:
                self.stream_call_count = 0
                self.nonstream_call_count = 0

            async def create(self, **kwargs):
                if kwargs.get("stream"):
                    self.stream_call_count += 1
                    return _PartialStream()
                self.nonstream_call_count += 1
                return SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=100,                       # SAME as the partial
                        output_tokens=42,
                        cache_creation_input_tokens=50,
                        cache_read_input_tokens=25,
                    ),
                    model="claude-haiku-4-5",
                    content=[_text_block("complete response")],
                    stop_reason="end_turn",
                )

        messages = _PartialThenFallbackMessages()
        client = _FakeClient(messages)

        events = []
        async for ev in call_model(
            [], CallModelOptions(model="claude-haiku-4-5"), client=client,
        ):
            events.append(ev)

        # The fallback path was exercised — verify by the FallbackEvent.
        fallback_events = [e for e in events if isinstance(e, FallbackEvent)]
        self.assertEqual(len(fallback_events), 1)

        # And by the actual non-streaming call.
        self.assertEqual(messages.stream_call_count, 1)
        self.assertEqual(messages.nonstream_call_count, 1)

        # Critical assertion: the trailing UsageEvent reports the REAL
        # response usage, NOT a 2× sum of partial + replay.
        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        self.assertEqual(len(usage_events), 1)
        final_usage = usage_events[0].usage
        self.assertEqual(final_usage.input_tokens, 100)            # not 200
        self.assertEqual(final_usage.output_tokens, 42)
        self.assertEqual(final_usage.cache_creation_input_tokens, 50)  # not 100
        self.assertEqual(final_usage.cache_read_input_tokens, 25)      # not 50

    async def test_caller_supplied_request_id_persists_into_fallback(self) -> None:
        from src.services.api.claude import CLIENT_REQUEST_ID_HEADER

        response = SimpleNamespace(
            usage=_usage(), model="claude-haiku-4-5",
            content=[_text_block("ok")], stop_reason="end_turn",
        )
        messages = _NonStreamMessages(response)
        client = _FakeClient(messages)

        # Caller pre-set the header. After the fallback runs we should see
        # the same caller-provided ID in the non-streaming request.
        opts = CallModelOptions(
            model="claude-haiku-4-5",
            extra_headers={CLIENT_REQUEST_ID_HEADER: "caller-id"},
        )
        async for _ in call_model([], opts, client=client):
            pass

        headers = (messages.last_kwargs or {}).get("extra_headers", {})
        self.assertEqual(headers.get(CLIENT_REQUEST_ID_HEADER), "caller-id")


if __name__ == "__main__":
    unittest.main()
