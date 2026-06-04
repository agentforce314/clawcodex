"""Regression tests for abort-signal-aware streaming in the Anthropic provider.

Before this fix, ``provider.chat_stream_response`` had no way to observe
a tripped abort controller. The agent loop's ``cancel_signal`` was only
checked between chunks via the optional ``on_text_chunk`` callback — but
``on_text_chunk`` never fires for a turn that emits tool_use blocks
without intervening text. The result: ESC during a multi-tool-use
response (e.g. the model emitting eight parallel ``Write`` calls) waited
the full model latency before the outer query loop's abort check fired,
producing the "ESC takes 20+ seconds" symptom on the default REPL UI.

The fix threads an ``AbortSignal`` through ``chat_stream_response``. The
Anthropic provider registers a listener that calls
``stream.response.close()`` when the signal fires; the SDK's blocking
socket read raises in the consumer thread, the provider catches it,
detects the abort via the signal state (not the exception type — the
SDK can raise several different classes depending on which syscall was
in flight), and re-raises ``AbortError``. The query loop's existing
abort-aware exception handler then routes through the same cancellation
processing as any other in-flight cancel.

These tests pin the provider-level contract using a synthetic stream
object that mimics the SDK's surface (``__enter__`` / ``__exit__`` /
``text_stream`` / ``response.close()``). We don't exercise the real
Anthropic SDK — that would require a live API key.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.providers.anthropic_provider import AnthropicProvider
from src.utils.abort_controller import AbortController, AbortError


class _FakeStream:
    """Minimal stand-in for the Anthropic SDK's ``messages.stream`` ctx manager.

    Yields text chunks one at a time with a configurable delay so we can
    simulate a slow streaming response that ESC needs to cancel.
    """

    def __init__(self, chunks: list[str], per_chunk_delay_s: float = 0.0):
        self._chunks = list(chunks)
        self._delay = per_chunk_delay_s
        self._closed = threading.Event()
        self.response = MagicMock()
        # The provider expects ``response.close`` to be callable.
        self.response.close.side_effect = self._closed.set

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @property
    def text_stream(self):
        for chunk in self._chunks:
            if self._closed.is_set():
                # SDK's iterator would raise once the underlying HTTP
                # response is closed — model that here so the provider
                # observes the same signal it would in production.
                raise ConnectionError("response closed mid-stream")
            if self._delay > 0:
                # Wait in small slices so a stream.close() landing from
                # another thread can interrupt promptly.
                deadline = time.monotonic() + self._delay
                while time.monotonic() < deadline:
                    if self._closed.is_set():
                        raise ConnectionError("response closed mid-stream")
                    time.sleep(0.005)
            yield chunk

    def get_final_message(self):
        # Build a minimal "final message" shape the provider's
        # ``_build_chat_response`` accepts; only called on the success
        # path, not after a close.
        m = MagicMock()
        m.content = []
        m.usage.input_tokens = 1
        m.usage.output_tokens = 1
        m.model = "test"
        m.stop_reason = "end_turn"
        return m


def _provider_with_stream(stream) -> AnthropicProvider:
    """Build a provider whose ``client.messages.stream`` returns ``stream``.

    We patch ``_ensure_client`` rather than the underlying SDK so the
    test stays insulated from the lazy-import / module-getattr dance in
    ``anthropic_provider.py``.
    """
    provider = AnthropicProvider(api_key="test", model="claude-sonnet-4-6")
    client = MagicMock()
    client.messages.stream.return_value = stream
    provider._ensure_client = lambda: client  # type: ignore[method-assign]
    return provider


def test_pre_aborted_signal_short_circuits_before_request(monkeypatch) -> None:
    """A tripped controller before the call bypasses the request entirely.

    Without this fast-path, an abort fired between turn boundaries
    would still spend the API round-trip before the outer loop could
    bail. The fast-path matches the same shape every other abort
    boundary in the codebase: detect, raise ``AbortError``, let the
    cancel boundary unwind.
    """
    controller = AbortController()
    controller.abort("user_interrupt")

    provider = AnthropicProvider(api_key="test", model="claude-sonnet-4-6")
    # ``_ensure_client`` must NOT be called — the fast-path bails first.
    provider._ensure_client = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("client should not be built when abort is pre-tripped")
    )

    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal=controller.signal,
        )
    provider._ensure_client.assert_not_called()


def test_mid_stream_abort_closes_stream_and_raises_abort_error() -> None:
    """ESC mid-stream → ``response.close()`` → iterator raises → ``AbortError``.

    This is the regression: previously the provider would propagate the
    iterator's exception generically, and the outer query loop would
    treat the closed-stream raise as a model error rather than a user
    cancel. The fix detects the abort via the signal state (which is
    authoritative even when the SDK exception class varies across
    SDK versions / socket libraries) and re-raises ``AbortError``.
    """
    controller = AbortController()
    # 5 chunks with 100ms delay each → 500ms total without abort.
    stream = _FakeStream(["a", "b", "c", "d", "e"], per_chunk_delay_s=0.10)
    provider = _provider_with_stream(stream)

    def _trip_after_first_chunk() -> None:
        # Sleep long enough that the provider has entered the stream
        # context and pulled at least one chunk, then fire the abort.
        time.sleep(0.15)
        controller.abort("user_interrupt")

    threading.Thread(target=_trip_after_first_chunk, daemon=True).start()

    start = time.monotonic()
    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal=controller.signal,
        )
    elapsed = time.monotonic() - start

    # The 5-chunk stream would take 500ms to complete. With abort, the
    # listener fires ~150ms in and the next iteration sees a closed
    # response and raises. Total elapsed should be well under 350ms;
    # 1s is comfortable CI headroom while still failing loudly if a
    # regression makes us wait out the full stream.
    assert elapsed < 1.0, f"abort took {elapsed:.2f}s — expected <1s"
    # The stream's response.close() must actually have been called —
    # this is what propagates the cancel into the SDK's blocking read.
    stream.response.close.assert_called()


def test_uncancelled_stream_returns_normally() -> None:
    """A never-tripped signal preserves existing streaming semantics."""
    controller = AbortController()  # never aborted
    stream = _FakeStream(["hello ", "world"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    seen_chunks: list[str] = []

    def _on_text(chunk: str) -> None:
        seen_chunks.append(chunk)

    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        on_text_chunk=_on_text,
        abort_signal=controller.signal,
    )

    # The chunks arrived through the callback (and the close listener
    # never fired, so the stream completes naturally).
    assert seen_chunks == ["hello ", "world"]
    stream.response.close.assert_not_called()
    # The provider returned the final structured response shape.
    assert response is not None


def test_no_abort_signal_param_preserves_legacy_callers() -> None:
    """SDK consumers that don't pass ``abort_signal`` see no behavior change.

    The parameter is keyword-only with default ``None`` so existing
    callers don't break. With ``None`` no listener is registered, no
    close fires, no AbortError can be raised by the provider — the
    stream completes naturally just like before this PR.
    """
    stream = _FakeStream(["ok"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        # No abort_signal — verifies the default works.
    )
    assert response is not None
    stream.response.close.assert_not_called()


def test_listener_detached_after_normal_completion() -> None:
    """The abort listener must not pin the provider alive across calls.

    Concrete failure mode the cleanup guards against: a long-lived
    AbortController (e.g. the REPL engine's controller, reused across
    many turns) would otherwise accumulate one dead listener per
    streaming call, and each abort would invoke N stream-close
    callbacks against long-gone streams.
    """
    controller = AbortController()
    stream = _FakeStream(["ok"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        abort_signal=controller.signal,
    )

    # No listeners should remain attached after the call completes.
    assert controller.signal._listeners == []
