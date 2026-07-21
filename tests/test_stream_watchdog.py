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
        watchdog = StreamWatchdog(stream, timeout_s=0.05)
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
        watchdog = StreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)
        # No exception escaped to this thread — the timer thread swallowed it.
        self.assertTrue(watchdog.fired)
        watchdog.disarm()

    def test_disarm_after_fire_is_safe(self):
        """``disarm`` can be called after the timer has already fired."""
        stream = MagicMock()
        watchdog = StreamWatchdog(stream, timeout_s=0.05)
        watchdog.arm()
        time.sleep(0.2)
        watchdog.disarm()  # Must not raise.

    def test_response_none_safe(self):
        """If the stream has no ``.response`` attribute, the timer no-ops cleanly."""
        stream = object()  # bare object, no response
        watchdog = StreamWatchdog(stream, timeout_s=0.05)
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
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_TIMEOUT_MS": "50"}), \
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
        with _patch.dict(os.environ, {"CLAUDE_STREAM_IDLE_TIMEOUT_MS": "50"}), \
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
        # Harness-classifiable phrasing (harbor: NetworkConnectionError).
        self.assertIn("Connection timed out", str(ctx.exception))

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

    def test_default_timeout_is_600s(self):
        self.assertEqual(self._chat_create_kwargs().get("timeout"), 600.0)

    def test_api_timeout_ms_env_override(self):
        kwargs = self._chat_create_kwargs(env={"API_TIMEOUT_MS": "120000"})
        self.assertEqual(kwargs.get("timeout"), 120.0)

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
