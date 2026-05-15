"""Regression tests for abort-signal-aware streaming in OpenAI-compatible providers.

Before this fix, ``OpenAICompatibleProvider.chat_stream_response`` only
had the pre-call fast-path from PR #144 — no mid-stream cancellation.
Users on LiteLLM / GLM / OpenAI / DeepSeek hitting ESC during a model
turn waited the full model latency before the outer query loop's
abort check fired, same 20+ second symptom this whole effort started
with on the Anthropic path.

This module pins the new behavior. We don't exercise the real OpenAI
SDK — a synthetic stream fake mimics the surface the provider reads
(``response.close()`` plus a slow-yielding iterator).
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.providers.openai_compatible import OpenAICompatibleProvider
from src.utils.abort_controller import AbortController, AbortError


class _FakeChoice:
    def __init__(self, content: str = "", finish_reason: str | None = None):
        self.delta = MagicMock()
        self.delta.content = content
        self.delta.reasoning_content = None
        self.delta.tool_calls = []
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, content: str = "", finish_reason: str | None = None):
        self.model = "test-model"
        self.usage = None
        self.choices = [_FakeChoice(content=content, finish_reason=finish_reason)]


class _FakeStream:
    """Mimic the OpenAI SDK's Stream object.

    Yields chunks with a configurable delay between them so the test
    can fire abort mid-iteration. ``response.close()`` sets an event
    the iterator polls — same mechanism the real httpx stream uses
    (closed socket raises on the next read).
    """

    def __init__(self, chunks: list[str], per_chunk_delay_s: float = 0.0):
        self._chunks = list(chunks)
        self._delay = per_chunk_delay_s
        self._closed = threading.Event()
        self.response = MagicMock()
        self.response.close.side_effect = self._closed.set

    def __iter__(self):
        for content in self._chunks:
            if self._closed.is_set():
                raise ConnectionError("stream response closed")
            if self._delay > 0:
                # Poll the closed event in small slices so a close()
                # landing from another thread interrupts within ~5ms.
                deadline = time.monotonic() + self._delay
                while time.monotonic() < deadline:
                    if self._closed.is_set():
                        raise ConnectionError("stream response closed")
                    time.sleep(0.005)
            yield _FakeChunk(content=content)
        yield _FakeChunk(finish_reason="stop")


class _ConcreteOpenAIProvider(OpenAICompatibleProvider):
    """Concrete subclass for testing — the base class is abstract."""

    def _create_client(self) -> Any:
        return MagicMock()

    def get_available_models(self) -> list[str]:
        return ["test-model"]


def _provider_with_stream(stream) -> _ConcreteOpenAIProvider:
    provider = _ConcreteOpenAIProvider(api_key="test", model="test-model")
    client = MagicMock()
    client.chat.completions.create.return_value = stream
    provider._client = client  # bypass the lazy create
    return provider


def test_pre_aborted_signal_short_circuits_before_request() -> None:
    """Already-tripped signal raises before the API round-trip."""
    controller = AbortController()
    controller.abort("user_interrupt")

    provider = _ConcreteOpenAIProvider(api_key="test", model="test-model")
    # The leaf method is what the provider actually calls; setting
    # ``side_effect`` on the root mock never fires (root is accessed
    # via attribute chain, not invoked). Setting it on the leaf gives
    # us a real failure sentinel if the fast-path regresses.
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = AssertionError(
        "client.chat.completions.create should not be invoked when abort is pre-tripped"
    )
    provider._client = fake_client

    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal=controller.signal,
        )
    # Belt and suspenders: explicit assertion that the leaf was never called.
    fake_client.chat.completions.create.assert_not_called()


def test_mid_stream_abort_closes_stream_and_raises_abort_error() -> None:
    """ESC mid-stream → ``response.close()`` → iterator raises → ``AbortError``.

    Same contract as the Anthropic test in
    ``test_provider_abort_signal.py``: an abort that fires while the
    SDK is blocked on the next chunk forces a close and raises
    ``AbortError`` within the poll cadence — orders of magnitude
    faster than the model's natural generation time.
    """
    controller = AbortController()
    # 5 chunks with 100ms delay each → 500ms total without abort.
    stream = _FakeStream(["a", "b", "c", "d", "e"], per_chunk_delay_s=0.10)
    provider = _provider_with_stream(stream)

    def _trip_after_first_chunk() -> None:
        # Sleep long enough that the provider has entered the stream
        # context and pulled at least one chunk, then trip the abort.
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
    # Without the fix this would have completed the full 500ms stream
    # before the next iteration's in-loop check OR the listener's
    # close took effect. With the fix the listener fires ~150ms in
    # and the iterator raises within one chunk-delay slice.
    assert elapsed < 1.0, f"abort took {elapsed:.2f}s — expected <1s"
    # The listener must have actually invoked response.close().
    stream.response.close.assert_called()


def test_in_loop_check_catches_abort_between_chunks() -> None:
    """Even with no chunk delay, the in-loop check observes abort.

    Models the case where chunks arrive back-to-back fast enough that
    the listener's close lands one iteration late (or that the model
    finished generating before the user pressed ESC, so the listener's
    socket close has nothing to interrupt). The in-loop check at the
    top of each ``for chunk in stream`` iteration must observe the
    abort and break out — otherwise the consumer would burn through
    whatever's left in the SDK's prefetch buffer.

    Load-bearing assertion: ``on_text_chunk`` is called with
    ``"first"`` but NOT with ``"second"``. Without this content check,
    the test would still pass even if the in-loop check were deleted:
    the second chunk would be consumed, the iterator would exit
    naturally, and the post-loop ``if abort_signal.aborted`` check
    would still raise ``AbortError``. The Critic pointed this out —
    asserting on the chunk callback list is what actually pins the
    in-loop defense.
    """
    controller = AbortController()

    class _TrippingStream:
        """Stream whose first yielded chunk trips the controller.

        ``__iter__`` is defined at class scope because Python looks
        up iteration protocol on the type, not the instance — an
        instance-level ``__iter__`` is silently ignored.
        """

        def __init__(self) -> None:
            self.response = MagicMock()

        def __iter__(self):
            yield _FakeChunk(content="first")
            controller.abort("user_interrupt")
            yield _FakeChunk(content="second")
            yield _FakeChunk(finish_reason="stop")

    stream = _TrippingStream()
    provider = _provider_with_stream(stream)

    seen: list[str] = []
    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            on_text_chunk=lambda c: seen.append(c),
            abort_signal=controller.signal,
        )

    # The in-loop check at the top of iteration 2 must observe the
    # abort and break BEFORE consuming "second". If this assertion
    # fails after the in-loop check is removed, we have a regression
    # back to the original "ESC waits for the model to finish
    # generating" behaviour.
    assert seen == ["first"], f"in-loop check leaked second chunk: {seen}"


def test_uncancelled_stream_returns_normally() -> None:
    """Never-tripped signal preserves existing streaming semantics."""
    controller = AbortController()  # never aborted
    stream = _FakeStream(["hello ", "world"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    seen: list[str] = []
    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        on_text_chunk=lambda c: seen.append(c),
        abort_signal=controller.signal,
    )

    assert seen == ["hello ", "world"]
    stream.response.close.assert_not_called()
    assert response.content == "hello world"


def test_no_abort_signal_param_preserves_legacy_callers() -> None:
    """``abort_signal=None`` default keeps the iterator working unchanged."""
    stream = _FakeStream(["ok"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
    )
    assert response.content == "ok"
    stream.response.close.assert_not_called()


def test_listener_detached_after_normal_completion() -> None:
    """The abort listener must not pin the provider alive across calls.

    Long-lived AbortControllers (the REPL engine's, reused across many
    turns) would otherwise accumulate one dead listener per streaming
    call, and each abort would invoke N stream-close callbacks against
    long-gone streams.
    """
    controller = AbortController()
    stream = _FakeStream(["ok"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        abort_signal=controller.signal,
    )

    assert controller.signal._listeners == []
