"""Regression tests for abort-signal-aware streaming in MinimaxProvider.

Minimax goes through the anthropic SDK against ``api.minimaxi.com/anthropic``,
so the close-listener pattern is structurally identical to
``AnthropicProvider.chat_stream_response``. These tests pin the
contract end-to-end against a synthetic stream fake — Minimax-specific
network plumbing isn't exercised, but the abort wiring is what we
care about.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.providers.minimax_provider import MinimaxProvider
from src.utils.abort_controller import AbortController, AbortError


class _FakeMessageStream:
    """Mimic the anthropic SDK's ``MessageStream`` context manager.

    Same surface ``AnthropicProvider.chat_stream_response`` reads:
    ``text_stream`` iterator, ``response.close()`` for forced
    teardown, ``get_final_message()`` for the post-stream
    structured response.
    """

    def __init__(self, chunks: list[str], per_chunk_delay_s: float = 0.0):
        self._chunks = list(chunks)
        self._delay = per_chunk_delay_s
        self._closed = threading.Event()
        self.response = MagicMock()
        self.response.close.side_effect = self._closed.set

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @property
    def text_stream(self):
        for chunk in self._chunks:
            if self._closed.is_set():
                raise ConnectionError("response closed mid-stream")
            if self._delay > 0:
                deadline = time.monotonic() + self._delay
                while time.monotonic() < deadline:
                    if self._closed.is_set():
                        raise ConnectionError("response closed mid-stream")
                    time.sleep(0.005)
            yield chunk

    def get_final_message(self):
        m = MagicMock()
        m.content = []
        m.usage.input_tokens = 1
        m.usage.output_tokens = 1
        m.model = "MiniMax-M2.7"
        m.stop_reason = "end_turn"
        return m


def _provider_with_stream(stream) -> MinimaxProvider:
    provider = MinimaxProvider(api_key="test", model="MiniMax-M2.7")
    client = MagicMock()
    client.messages.stream.return_value = stream
    provider._ensure_client = lambda: client  # type: ignore[method-assign]
    return provider


def test_pre_aborted_signal_short_circuits_before_request() -> None:
    """Already-tripped signal raises before the API round-trip."""
    controller = AbortController()
    controller.abort("user_interrupt")

    provider = MinimaxProvider(api_key="test", model="MiniMax-M2.7")
    provider._ensure_client = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError(
            "_ensure_client should not be called when abort is pre-tripped"
        )
    )

    with pytest.raises(AbortError):
        provider.chat_stream_response(
            messages=[{"role": "user", "content": "hi"}],
            abort_signal=controller.signal,
        )
    provider._ensure_client.assert_not_called()


def test_mid_stream_abort_closes_stream_and_raises_abort_error() -> None:
    """ESC mid-stream → ``response.close()`` → iterator raises → ``AbortError``."""
    controller = AbortController()
    stream = _FakeMessageStream(["a", "b", "c", "d", "e"], per_chunk_delay_s=0.10)
    provider = _provider_with_stream(stream)

    def _trip_after_first_chunk() -> None:
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
    assert elapsed < 1.0, f"abort took {elapsed:.2f}s — expected <1s"
    stream.response.close.assert_called()


def test_uncancelled_stream_returns_normally() -> None:
    """Never-tripped signal preserves existing streaming semantics."""
    controller = AbortController()
    stream = _FakeMessageStream(["hello ", "world"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    seen: list[str] = []
    response = provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        on_text_chunk=lambda c: seen.append(c),
        abort_signal=controller.signal,
    )

    assert seen == ["hello ", "world"]
    stream.response.close.assert_not_called()
    assert response is not None


def test_listener_detached_after_normal_completion() -> None:
    """The listener must be removed after a clean stream exit."""
    controller = AbortController()
    stream = _FakeMessageStream(["ok"], per_chunk_delay_s=0.0)
    provider = _provider_with_stream(stream)

    provider.chat_stream_response(
        messages=[{"role": "user", "content": "hi"}],
        abort_signal=controller.signal,
    )

    assert controller.signal._listeners == []
