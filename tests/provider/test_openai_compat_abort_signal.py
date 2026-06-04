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
    # And on the in-loop-break path, the underlying httpx response
    # must still be closed (otherwise the socket leaks). The
    # listener fired during the synchronous ``controller.abort()``
    # call inside the iterator, so close() was already invoked once
    # there; the helper's ``__exit__`` close-on-abort guarantee adds
    # a second idempotent call. We just assert at-least-once.
    assert stream.response.close.called, "stream.response was not closed on in-loop break"


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


class _StuckStream:
    """Mimic an OpenAI Stream whose iterator never honors ``response.close()``.

    Models the LiteLLM/proxy scenario reported by the user: the
    underlying socket is not interrupted when ``stream.response.close()``
    is called from another thread, so the SDK iterator stays blocked
    on the next chunk indefinitely. The worker-thread iteration in
    ``OpenAICompatibleProvider.chat_stream_response`` must NOT rely on
    the iterator unblocking — the main thread polls a queue with
    timeout and bails on abort.

    ``__iter__`` blocks on an ``Event`` that the test never sets, so
    iteration would hang forever without the worker+queue decoupling.
    """

    def __init__(self) -> None:
        self.response = MagicMock()
        self._never_set = threading.Event()
        self._iter_entered = threading.Event()

    def __iter__(self):
        self._iter_entered.set()
        # Block forever — even if response.close() is called.
        # ``_never_set`` is never set in this test.
        self._never_set.wait()
        # Unreachable. If we somehow get here, yield nothing so the
        # iterator ends and the test doesn't go on forever.
        return
        yield  # pragma: no cover


def test_abort_unwinds_promptly_even_when_iterator_never_returns() -> None:
    """The user's bug: ESC must unwind in <1s even when the SDK never honors close().

    Pre-fix (single-threaded ``for chunk in stream``): the main thread
    was blocked on ``next(stream)`` waiting for a chunk the LiteLLM
    proxy never delivered, ``response.close()`` from the listener
    thread didn't propagate to the kernel socket read, and ESC waited
    indefinitely.

    Post-fix (worker thread + queue): the SDK iteration runs on a
    daemon worker that gets orphaned on abort. The main thread polls
    the queue with a 100 ms timeout and bails on ``guard.aborted``.
    Total ESC-to-AbortError budget is one poll tick plus listener
    cascade — well under 1 second on any reasonable machine.

    Failure mode this regression-tests against: someone reverting the
    worker+queue would make the main thread block on ``next(stream)``
    again. With ``_StuckStream``'s never-set Event, the test would
    hang forever (the assertion-failure form is a CI timeout, not a
    fast fail — but a CI timeout is still loud).
    """
    controller = AbortController()
    stream = _StuckStream()
    provider = _provider_with_stream(stream)

    def _trip_after_worker_starts() -> None:
        # Wait for the worker thread to actually enter the iterator,
        # so the test pins "abort during a stuck iteration" rather
        # than "abort before the worker started".
        assert stream._iter_entered.wait(timeout=2.0), "worker never entered iterator"
        controller.abort("user_interrupt")

    threading.Thread(target=_trip_after_worker_starts, daemon=True).start()

    start = time.monotonic()
    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal=controller.signal,
        )
    elapsed = time.monotonic() - start

    # 100 ms poll tick + listener cascade + abort propagation. 1.5 s
    # is comfortable headroom on slow CI; on a healthy laptop this is
    # well under 300 ms.
    assert elapsed < 1.5, f"abort took {elapsed:.2f}s — expected <1.5s"


class _ContentThenUsageStream:
    """Stream that yields one content chunk then a final usage-only chunk.

    Mirrors OpenAI's streaming wire format when
    ``stream_options.include_usage=True``: content/delta chunks first,
    then a final chunk with empty ``choices`` and populated ``usage``.
    """

    def __init__(self) -> None:
        self.response = MagicMock()

    def __iter__(self):
        # Regular content chunk.
        yield _FakeChunk(content="hello")
        # Final usage-only chunk: empty choices, populated usage.
        final = MagicMock()
        final.model = "test-model"
        final.choices = []
        final.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
        )
        yield final


def test_normal_completion_still_captures_final_usage() -> None:
    """The worker+queue path must not drop the final usage chunk.

    OpenAI emits usage stats only in the last chunk (with empty
    ``choices``). The main thread must drain every queued chunk
    before breaking on ``_DONE`` — otherwise token counting would
    silently regress for non-aborted streams.
    """
    controller = AbortController()
    stream = _ContentThenUsageStream()
    provider = _provider_with_stream(stream)

    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        abort_signal=controller.signal,
    )
    assert response.content == "hello"
    # The final usage chunk made it through the queue; otherwise
    # ``response.usage`` would be the default empty dict, and the
    # ``↓ N tokens`` REPL spinner would silently lose count.
    assert response.usage.get("total_tokens") == 15
