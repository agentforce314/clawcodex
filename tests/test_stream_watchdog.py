"""WI-5.2 acceptance tests — streaming watchdog.

The chapter's pattern (TS ``claude.ts:1922``): if no chunks arrive for
``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` (default 90 s), abort the stream and
fall back to non-streaming. Python uses ``threading.Timer`` to schedule
the deadline (per the plan's WI-5.2 decision; asyncio was rejected
because the SDK is sync).
"""

from __future__ import annotations

import os
import threading
import time
import unittest

import pytest
from unittest.mock import MagicMock

from src.utils.stream_watchdog import (
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    StreamWatchdog,
    stream_idle_timeout_seconds,
)


class TestStreamIdleTimeoutResolution(unittest.TestCase):
    """Env-var resolution for ``CLAUDE_STREAM_IDLE_TIMEOUT_MS``."""

    def tearDown(self):
        os.environ.pop("CLAUDE_STREAM_IDLE_TIMEOUT_MS", None)

    def test_default_when_unset(self):
        os.environ.pop("CLAUDE_STREAM_IDLE_TIMEOUT_MS", None)
        self.assertEqual(stream_idle_timeout_seconds(), DEFAULT_STREAM_IDLE_TIMEOUT_S)
        self.assertEqual(DEFAULT_STREAM_IDLE_TIMEOUT_S, 90.0)

    def test_env_var_in_milliseconds(self):
        os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "30000"
        self.assertEqual(stream_idle_timeout_seconds(), 30.0)

    def test_malformed_env_falls_back_to_default(self):
        os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "not-a-number"
        self.assertEqual(stream_idle_timeout_seconds(), DEFAULT_STREAM_IDLE_TIMEOUT_S)

    def test_zero_falls_back_to_default(self):
        os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "0"
        self.assertEqual(stream_idle_timeout_seconds(), DEFAULT_STREAM_IDLE_TIMEOUT_S)

    def test_negative_falls_back_to_default(self):
        os.environ["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "-1"
        self.assertEqual(stream_idle_timeout_seconds(), DEFAULT_STREAM_IDLE_TIMEOUT_S)


class TestStreamWatchdogFires(unittest.TestCase):
    """The watchdog fires after the idle deadline and closes the stream."""

    def test_timer_does_not_fire_when_disarmed_quickly(self):
        """``arm`` then ``disarm`` within the timeout → ``fired`` stays False."""
        stream = MagicMock()
        watchdog = StreamWatchdog(stream, timeout_s=10.0)
        watchdog.arm()
        watchdog.disarm()
        # Tiny sleep to ensure any racing timer would have fired.
        time.sleep(0.05)
        self.assertFalse(watchdog.fired)
        stream.response.close.assert_not_called()

    def test_timer_fires_after_short_timeout(self):
        """Set a 50ms timeout, don't reset, wait 200ms → fired + close called."""
        stream = MagicMock()
        watchdog = StreamWatchdog(stream, timeout_s=0.05, first_event_timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)  # wait past the deadline
        self.assertTrue(watchdog.fired)
        stream.response.close.assert_called_once()
        watchdog.disarm()  # cleanup

    def test_reset_pushes_deadline_forward(self):
        """Periodic ``reset`` calls prevent the timer from firing."""
        stream = MagicMock()
        watchdog = StreamWatchdog(stream, timeout_s=0.1)
        watchdog.arm()
        # Reset every 30ms for 200ms total → never let the deadline lapse.
        for _ in range(7):
            time.sleep(0.03)
            watchdog.reset()
        watchdog.disarm()
        self.assertFalse(watchdog.fired)
        stream.response.close.assert_not_called()

    def test_close_failure_does_not_propagate(self):
        """If ``response.close`` raises, the timer thread swallows it."""
        stream = MagicMock()
        stream.response.close.side_effect = RuntimeError("simulated close failure")
        watchdog = StreamWatchdog(stream, timeout_s=0.05, first_event_timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)
        # No exception escaped to this thread — the timer thread swallowed it.
        self.assertTrue(watchdog.fired)
        watchdog.disarm()

    def test_disarm_after_fire_is_safe(self):
        """``disarm`` can be called after the timer has already fired."""
        stream = MagicMock()
        watchdog = StreamWatchdog(stream, timeout_s=0.05, first_event_timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)
        watchdog.disarm()  # Must not raise.

    def test_response_none_safe(self):
        """If the stream has no ``.response`` attribute, the timer no-ops cleanly."""
        stream = object()  # bare object, no response
        watchdog = StreamWatchdog(stream, timeout_s=0.05, first_event_timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)
        self.assertTrue(watchdog.fired)
        watchdog.disarm()


def _stalling_stream_cm():
    """A stream context-manager whose event iteration stalls long enough
    for a 50ms watchdog to fire, then raises (mirrors ``close()``
    interrupting the iterator). The provider drives the watchdog from the
    full event stream (``for event in stream``), so the stall happens on
    ``__iter__``."""
    fake_response = MagicMock()
    fake_response.close = MagicMock()

    def slow_event_stream():
        time.sleep(0.3)
        raise RuntimeError("stream closed by watchdog")
        yield  # unreachable; makes this a generator

    fake_stream = MagicMock()
    fake_stream.__iter__ = MagicMock(return_value=slow_event_stream())
    fake_stream.response = fake_response

    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=fake_stream)
    stream_cm.__exit__ = MagicMock(return_value=False)
    return stream_cm, fake_response


def _healthy_empty_stream_cm():
    """A stream that completes immediately with no events; the provider
    builds a ChatResponse from the (empty) accumulated text when
    ``get_final_message`` fails."""
    fake_stream = MagicMock()
    fake_stream.__iter__ = MagicMock(return_value=iter(()))
    fake_stream.response = MagicMock()
    fake_stream.get_final_message = MagicMock(side_effect=RuntimeError("n/a"))

    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=fake_stream)
    stream_cm.__exit__ = MagicMock(return_value=False)
    return stream_cm


class TestTwoPhaseWatchdog(unittest.TestCase):
    """First event gets a longer grace (prompt processing); later events
    the tighter inter-event idle. This is the terminal-bench fix: a flat
    90s timeout fired during legitimate time-to-first-event on large
    (1M-eligible) agentic requests where the SDK hides ping keepalives."""

    def test_first_event_grace_env_resolution(self):
        from unittest.mock import patch as _patch
        from src.utils.stream_watchdog import (
            stream_first_event_timeout_seconds,
            DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S,
        )

        with _patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS", None)
            os.environ.pop("CLAUDE_STREAM_IDLE_TIMEOUT_MS", None)
            self.assertEqual(
                stream_first_event_timeout_seconds(),
                DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S,
            )
        with _patch.dict(os.environ, {"CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS": "120000"}):
            self.assertEqual(stream_first_event_timeout_seconds(), 120.0)
        # Never stricter than the inter-event timeout.
        with _patch.dict(os.environ, {
            "CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS": "10000",
            "CLAUDE_STREAM_IDLE_TIMEOUT_MS": "90000",
        }):
            self.assertEqual(stream_first_event_timeout_seconds(), 90.0)

    def test_survives_first_event_wait_then_fires_on_inter_event_idle(self):
        from src.utils.stream_watchdog import StreamWatchdog

        resp = MagicMock()
        stream = MagicMock()
        stream.response = resp
        # first-event grace 800ms, inter-event 100ms (wide slack so a CI
        # scheduling stall can't flip the "must not fire in grace" assert).
        watchdog = StreamWatchdog(
            stream, timeout_s=0.1, first_event_timeout_s=0.8
        )
        watchdog.arm()
        time.sleep(0.35)  # past inter-event, comfortably within first grace
        self.assertFalse(watchdog.fired, "must not fire during first-event grace")
        resp.close.assert_not_called()
        watchdog.reset()  # first event arrived → tighten to inter-event
        time.sleep(0.25)  # past inter-event now
        self.assertTrue(watchdog.fired, "must fire on inter-event idle after start")
        resp.close.assert_called()
        watchdog.disarm()

    def test_first_event_timeout_still_fires_a_truly_dead_stream(self):
        from src.utils.stream_watchdog import StreamWatchdog

        resp = MagicMock()
        stream = MagicMock()
        stream.response = resp
        watchdog = StreamWatchdog(
            stream, timeout_s=0.1, first_event_timeout_s=0.2
        )
        watchdog.arm()
        time.sleep(0.35)  # never any event; first-event grace lapses
        self.assertTrue(watchdog.fired)
        resp.close.assert_called()
        watchdog.disarm()


class TestPingAwareLiveness(unittest.TestCase):
    """Byte progress on the HTTP response keeps the watchdog from killing a
    healthy-but-slow stream — the pings the SDK drops from the typed stream
    are still bytes httpx counts. This is the primary terminal-bench fix."""

    def test_byte_progress_prevents_fire(self):
        from src.utils.stream_watchdog import StreamWatchdog

        response = MagicMock()
        response.num_bytes_downloaded = 0
        stream = MagicMock()
        stream.response = response
        watchdog = StreamWatchdog(
            stream, timeout_s=0.08, first_event_timeout_s=0.08
        )
        watchdog.arm()
        # Simulate keepalive pings arriving as bytes across ~5 deadlines,
        # WITHOUT any typed event (no reset() call).
        for _ in range(6):
            time.sleep(0.05)
            response.num_bytes_downloaded += 128
        self.assertFalse(watchdog.fired, "byte progress must re-arm, not fire")
        response.close.assert_not_called()
        # Once bytes stop, the next deadline fires.
        time.sleep(0.2)
        self.assertTrue(watchdog.fired)
        response.close.assert_called()
        watchdog.disarm()

    def test_no_byte_progress_still_fires(self):
        from src.utils.stream_watchdog import StreamWatchdog

        response = MagicMock()
        response.num_bytes_downloaded = 500  # constant → no progress
        stream = MagicMock()
        stream.response = response
        watchdog = StreamWatchdog(
            stream, timeout_s=0.08, first_event_timeout_s=0.08
        )
        watchdog.arm()
        time.sleep(0.25)
        self.assertTrue(watchdog.fired)
        response.close.assert_called()
        watchdog.disarm()


class TestWatchdogIntegrationWithProvider(unittest.TestCase):
    """End-to-end checks of the watchdog RECOVERY path.

    Recovery = retry the STREAM, never a non-streaming re-issue: the
    Anthropic SDK refuses non-streaming requests at opus-class
    ``max_tokens`` ("Streaming is required for operations that may take
    longer than 10 minutes"), which made the old fallback fatal exactly
    when it engaged (18/89 terminal-bench trials, 2026-07-19).
    """

    def test_watchdog_fire_retries_stream_then_succeeds(self):
        from unittest.mock import patch as _patch
        from src.providers.anthropic_provider import AnthropicProvider
        from src.providers.base import ChatResponse

        stall_cm, stall_response = _stalling_stream_cm()
        good_cm = _healthy_empty_stream_cm()

        fake_client = MagicMock()
        fake_client.messages.stream.side_effect = [stall_cm, good_cm]

        provider = AnthropicProvider(api_key="test")
        provider.client = fake_client
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_TIMEOUT_MS": "50",
                                       "CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS": "50"}), \
             _patch.object(provider, "chat") as mock_chat:
            result = provider.chat_stream_response(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                on_text_chunk=None,
            )

        self.assertIsInstance(result, ChatResponse)
        # The watchdog actually fired on attempt 1...
        stall_response.close.assert_called()
        # ...and recovery was a SECOND STREAM, never non-streaming chat().
        self.assertEqual(fake_client.messages.stream.call_count, 2)
        mock_chat.assert_not_called()

    def test_watchdog_exhaustion_raises_stream_idle_timeout(self):
        from unittest.mock import patch as _patch
        from src.providers.anthropic_provider import AnthropicProvider
        from src.utils.stream_watchdog import StreamIdleTimeout

        cms = [_stalling_stream_cm()[0] for _ in range(3)]
        fake_client = MagicMock()
        fake_client.messages.stream.side_effect = cms

        provider = AnthropicProvider(api_key="test")
        provider.client = fake_client
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_TIMEOUT_MS": "50",
                                       "CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS": "50"}), \
             _patch.object(provider, "chat") as mock_chat:
            with self.assertRaises(StreamIdleTimeout) as ctx:
                provider.chat_stream_response(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    on_text_chunk=None,
                )

        # Default budget: 3 total attempts, all streamed.
        self.assertEqual(fake_client.messages.stream.call_count, 3)
        mock_chat.assert_not_called()
        # The message describes the actual failure and nothing else. It
        # used to append "Connection timed out" purely so an eval harness
        # would classify the trial as a retryable network error — wording
        # aimed at a grader, not a reader, and inaccurate besides (an idle
        # stream is not a connection timeout). Pin the honest phrasing so
        # it does not creep back.
        message = str(ctx.exception)
        self.assertIn("stream idle timeout", message)
        self.assertIn("no stream events for", message)
        self.assertNotIn("Connection timed out", message)

    def test_retry_budget_env_override(self):
        from src.utils.stream_watchdog import stream_idle_max_attempts
        from unittest.mock import patch as _patch

        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_MAX_RETRIES": "0"}):
            self.assertEqual(stream_idle_max_attempts(), 1)
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_MAX_RETRIES": "5"}):
            self.assertEqual(stream_idle_max_attempts(), 6)
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_MAX_RETRIES": "junk"}):
            self.assertEqual(stream_idle_max_attempts(), 3)
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_MAX_RETRIES": "-2"}):
            self.assertEqual(stream_idle_max_attempts(), 3)


class TestChatExplicitTimeout(unittest.TestCase):
    """Non-streaming ``chat()`` must pass an explicit per-request timeout.

    Without one, the Anthropic SDK refuses large-``max_tokens``
    non-streaming requests outright (the >10-minute guard) — the failure
    mode that turned every legacy watchdog fallback into a fatal error.
    """

    def _chat_create_kwargs(self, env=None):
        from unittest.mock import patch as _patch
        from src.providers.anthropic_provider import AnthropicProvider

        fake_client = MagicMock()
        fake_client.messages.create.return_value = MagicMock(
            content=[], usage=None, stop_reason="end_turn", model="m"
        )
        provider = AnthropicProvider(api_key="test")
        provider.client = fake_client
        with _patch.dict(os.environ, env or {}, clear=False):
            provider.chat(messages=[{"role": "user", "content": "hi"}])
        return fake_client.messages.create.call_args.kwargs

    # The value is a phase-split ``httpx.Timeout``, not a bare float. httpx
    # expands a float across all four phases, so passing one here silently reset
    # ``connect`` from the client-level 15s back to the 600s read budget — a
    # black-holed SYN on a compaction summarize then hung for ten minutes.
    # Assert per phase; the class's contract (a timeout IS set, so the SDK
    # accepts large-``max_tokens`` non-streaming calls) is unchanged.
    def test_default_timeout_is_600s(self):
        from src.providers.anthropic_provider import DEFAULT_API_CONNECT_TIMEOUT_S

        timeout = self._chat_create_kwargs().get("timeout")
        self.assertEqual(timeout.read, 600.0)
        self.assertEqual(timeout.connect, DEFAULT_API_CONNECT_TIMEOUT_S)

    def test_api_timeout_ms_env_override(self):
        from src.providers.anthropic_provider import DEFAULT_API_CONNECT_TIMEOUT_S

        kwargs = self._chat_create_kwargs(env={"API_TIMEOUT_MS": "120000"})
        timeout = kwargs.get("timeout")
        self.assertEqual(timeout.read, 120.0)
        # The env var governs the generation budget only, not the handshake.
        self.assertEqual(timeout.connect, DEFAULT_API_CONNECT_TIMEOUT_S)

    def test_caller_supplied_timeout_wins(self):
        from src.providers.anthropic_provider import AnthropicProvider

        fake_client = MagicMock()
        fake_client.messages.create.return_value = MagicMock(
            content=[], usage=None, stop_reason="end_turn", model="m"
        )
        provider = AnthropicProvider(api_key="test")
        provider.client = fake_client
        provider.chat(messages=[{"role": "user", "content": "hi"}], timeout=42.0)
        self.assertEqual(
            fake_client.messages.create.call_args.kwargs.get("timeout"), 42.0
        )


class TestForceCloseResponse(unittest.TestCase):
    """``force_close_response`` — the shutdown-then-close contract.

    A bare ``response.close()`` from another thread does NOT wake a
    consumer blocked in ``recv``/``ssl.read`` (observed live: agent-server
    ``interrupt`` mid-Anthropic-stream stopped the deltas but the worker
    thread never unwound). The fix shuts the underlying socket down
    first — ``shutdown(SHUT_RDWR)`` is the documented cross-thread way
    to interrupt a blocked read.
    """

    def test_shuts_socket_down_before_closing(self):
        from src.utils.stream_watchdog import force_close_response
        import socket as socket_mod

        calls = []
        sock = MagicMock()
        sock.shutdown.side_effect = lambda how: calls.append(("shutdown", how))
        network_stream = MagicMock()
        network_stream.get_extra_info.return_value = sock
        response = MagicMock()
        response.extensions = {"network_stream": network_stream}
        response.close.side_effect = lambda: calls.append(("close", None))
        stream = MagicMock()
        stream.response = response

        force_close_response(stream)

        network_stream.get_extra_info.assert_called_once_with("socket")
        self.assertEqual(
            calls,
            [("shutdown", socket_mod.SHUT_RDWR), ("close", None)],
        )

    def test_close_still_runs_without_the_network_stream_extension(self):
        from src.utils.stream_watchdog import force_close_response

        response = MagicMock()
        response.extensions = {}
        stream = MagicMock()
        stream.response = response

        force_close_response(stream)

        response.close.assert_called_once()

    def test_shutdown_failure_does_not_block_the_close(self):
        from src.utils.stream_watchdog import force_close_response

        sock = MagicMock()
        sock.shutdown.side_effect = OSError("already shut down")
        network_stream = MagicMock()
        network_stream.get_extra_info.return_value = sock
        response = MagicMock()
        response.extensions = {"network_stream": network_stream}
        stream = MagicMock()
        stream.response = response

        force_close_response(stream)  # must not raise

        response.close.assert_called_once()

    def test_never_raises_without_a_response(self):
        from src.utils.stream_watchdog import force_close_response

        force_close_response(MagicMock(response=None))
        force_close_response(object())  # no .response attribute at all

    def test_unblocks_a_reader_parked_on_a_real_socket(self):
        """The live-hang regression: a thread blocked in ``recv`` on a real
        socket must unwind promptly once ``force_close_response`` runs.

        Uses a plain TCP pair (the syscall semantics that caused the hang
        are at the socket layer; TLS only wraps them) and an httpcore-shaped
        stub exposing the socket via ``extensions['network_stream']``.
        """
        import socket as socket_mod

        from src.utils.stream_watchdog import force_close_response

        server, client = socket_mod.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)

        unwound = threading.Event()

        def blocked_reader():
            try:
                client.recv(65536)  # server never sends — blocks
            except Exception:
                pass
            unwound.set()

        reader = threading.Thread(target=blocked_reader, daemon=True)
        reader.start()
        time.sleep(0.1)  # let the reader park inside recv
        self.assertFalse(unwound.is_set(), "reader must be blocked before the close")

        network_stream = MagicMock()
        network_stream.get_extra_info.return_value = client
        response = MagicMock()
        response.extensions = {"network_stream": network_stream}
        stream = MagicMock()
        stream.response = response

        force_close_response(stream)

        self.assertTrue(
            unwound.wait(timeout=2.0),
            "shutdown(SHUT_RDWR) must wake the blocked recv",
        )


if __name__ == "__main__":
    unittest.main()


class TestTransientStreamDropClassifier(unittest.TestCase):
    """A mid-stream transport drop is retryable; a server verdict is not.

    Terminal-bench 2.1 (regex-chess, 2026-07-25): one ``peer closed
    connection without sending complete message body (incomplete chunked
    read)`` ended a 24-minute trial at reward 0, moments after a passing
    1500-position fuzz run. The idle watchdog already re-attempts the
    stream for the equivalent condition; this classifier is what lets the
    transport case take the same bounded path instead of propagating.
    """

    def _classify(self, exc):
        from src.providers.anthropic_provider import _is_transient_stream_drop

        return _is_transient_stream_drop(exc)

    def test_retries_transport_drops(self):
        class APIConnectionError(Exception):
            pass

        for exc in (
            APIConnectionError("peer closed connection without sending "
                               "complete message body (incomplete chunked read)"),
            Exception("Server disconnected without sending a response."),
            Exception("Connection reset by peer"),
        ):
            self.assertTrue(self._classify(exc), exc)

    def test_never_retries_status_bearing_errors(self):
        """Auth and overload carry an HTTP status — retrying an expired or
        revoked token just burns the remaining budget on guaranteed 401s."""
        class APIStatusError(Exception):
            def __init__(self, message, status_code):
                super().__init__(message)
                self.status_code = status_code

        for exc in (
            APIStatusError("OAuth access token has been revoked.", 401),
            APIStatusError("overloaded_error", 529),
            APIStatusError("invalid_request_error", 400),
        ):
            self.assertFalse(self._classify(exc), exc)

    def test_matches_the_real_sdk_and_httpx_classes(self):
        """Pin against REAL exception objects, not local stand-ins.

        The local fakes validate name matching but cannot catch SDK
        hierarchy drift — which is the actual risk, since the predicate is
        subclass-blind by design. httpx.RemoteProtocolError is the concrete
        class the regex-chess trial died on: the SDK does not wrap errors
        raised while ITERATING a stream, so it arrives raw.
        """
        httpx = pytest.importorskip("httpx")
        anthropic = pytest.importorskip("anthropic")

        self.assertTrue(self._classify(httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)")))
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        self.assertTrue(self._classify(httpx.ReadError("boom", request=req)))
        self.assertTrue(self._classify(
            anthropic.APIConnectionError(request=req)))
        # Client-side protocol bugs are OUR fault — replaying just repeats it.
        self.assertFalse(self._classify(httpx.LocalProtocolError("bad header")))

    def test_never_retries_unrelated_failures(self):
        for exc in (KeyboardInterrupt(), ValueError("bad input"),
                    Exception("prompt is too long")):
            self.assertFalse(self._classify(exc), exc)

    def test_status_bearing_wins_over_message_match(self):
        """A status-bearing error whose text happens to mention a drop is
        still a server verdict — the status check must come first."""
        class Weird(Exception):
            status_code = 400

        self.assertFalse(self._classify(Weird("peer closed connection")))


class TestTransientDropRetryLoop(unittest.TestCase):
    """The classifier is only useful if the attempt loop acts on it."""

    def _provider(self):
        from src.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key="sk-test", model="claude-opus-5")

    def _call(self, provider, side_effect, on_text_chunk=None):
        from unittest.mock import patch

        with patch.object(
            type(provider), "_stream_attempt", side_effect=side_effect
        ) as attempt, patch.object(
            type(provider), "_client_for_request", return_value=MagicMock()
        ), patch.object(
            type(provider), "_merge_beta_headers"
        ), patch.object(
            type(provider), "_merge_request_id", return_value="req-test"
        ):
            try:
                result = provider.chat_stream_response(
                    [{"role": "user", "content": "hi"}],
                    on_text_chunk=on_text_chunk,
                )
            except Exception as exc:  # noqa: BLE001 — the assertion subject
                return attempt, exc
            return attempt, result

    def test_retries_then_succeeds(self):
        drop = Exception("peer closed connection without sending complete "
                         "message body (incomplete chunked read)")
        sentinel = MagicMock(name="chat-response")
        attempt, result = self._call(self._provider(), [drop, sentinel])
        self.assertIs(result, sentinel)
        self.assertEqual(attempt.call_count, 2, "should re-attempt after a drop")

    def test_does_not_retry_after_text_was_already_emitted(self):
        """A retry replays the response, so retrying after partial output
        would emit those chunks TWICE — what query.py:1635 forbids
        ("never after partial output"). That check lives one layer up and
        cannot see a retry taken here, so this gate must hold locally.

        Harbor runs without --include-partial-messages, so on_text_chunk is
        None in a scored trial and the rescue still fires; this only
        protects the TUI/SDK surfaces that do stream chunks.
        """
        seen = []
        drop = Exception("peer closed connection without sending complete "
                         "message body (incomplete chunked read)")

        def emit_then_drop(*a, **k):
            cb = k.get("on_text_chunk")
            if cb:
                cb("partial ")
            raise drop

        provider = self._provider()
        attempt, exc = self._call(
            provider, emit_then_drop, on_text_chunk=seen.append
        )
        self.assertIn("peer closed connection", str(exc))
        self.assertEqual(attempt.call_count, 1, "must not replay after output")
        self.assertEqual(seen, ["partial "], "no duplicated text")

    def test_retries_when_the_drop_happened_before_any_output(self):
        """The eval case: nothing streamed yet, so replay is safe."""
        seen = []
        sentinel = MagicMock(name="chat-response")
        drop = Exception("peer closed connection (incomplete chunked read)")
        attempt, result = self._call(
            self._provider(), [drop, sentinel], on_text_chunk=seen.append
        )
        self.assertIs(result, sentinel)
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(seen, [])

    def test_last_attempt_reraises_the_real_error_not_idle_timeout(self):
        """Exhausting attempts must surface the transport error itself —
        reporting 'stream idle timeout' for a connection drop sends whoever
        reads the log looking for the wrong bug."""
        drop = Exception("peer closed connection without sending complete "
                         "message body (incomplete chunked read)")
        attempt, exc = self._call(self._provider(), [drop, drop, drop])
        self.assertIn("peer closed connection", str(exc))
        self.assertNotIn("idle timeout", str(exc).lower())
        self.assertEqual(attempt.call_count, 3, "must exhaust the attempts")

    def test_auth_error_fails_fast_without_retrying(self):
        class APIStatusError(Exception):
            def __init__(self, message, status_code):
                super().__init__(message)
                self.status_code = status_code

        err = APIStatusError("OAuth access token has been revoked.", 401)
        attempt, exc = self._call(self._provider(), [err, err, err])
        self.assertIn("revoked", str(exc))
        self.assertEqual(attempt.call_count, 1, "auth failures must not retry")
