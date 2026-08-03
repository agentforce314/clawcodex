"""A stalled stream on the OpenAI-compatible wire must not hang forever.

The Anthropic wire has had an idle watchdog since WI-5.2. This wire had no
elapsed-time bound at all, and on terminal-bench 2.1 (2026-08-02)
``model-extraction-relu-logits`` sat for 880 SECONDS of total silence after
one tool call before the harness killed the trial at its 900 s ceiling.

The httpx ``read`` timeout does not cover it: ``read`` is the gap between
BYTES, and SSE keepalives are bytes. So the deadline here is measured on
SEMANTIC deltas, and the tests below pin both directions —

  * a stream that keeps delivering frames but no deltas MUST fire, and
  * a slow-but-productive stream MUST NOT (the "don't hurt other models"
    guard; that one is the whole reason the threshold is the 300 s
    first-event grace rather than the 90 s inter-event idle).
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from src.providers.openai_compatible import OpenAICompatibleProvider
from src.utils.stream_watchdog import StreamIdleTimeout


# Deadline used by every test here. Real default is 300 s; these override the
# env so the suite stays fast. BOTH vars are needed —
# ``stream_first_event_timeout_seconds`` floors its result at the inter-event
# idle, so setting only the first-event var leaves it pinned at 90 s.
_DEADLINE_MS = 300
_DEADLINE_S = _DEADLINE_MS / 1000.0


@pytest.fixture(autouse=True)
def _fast_deadline(monkeypatch):
    monkeypatch.setenv("CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS", str(_DEADLINE_MS))
    monkeypatch.setenv("CLAUDE_STREAM_IDLE_TIMEOUT_MS", str(_DEADLINE_MS))


def _chunk(*, content=None, reasoning=None, finish=None):
    """One streaming chunk in the shape the consumer duck-types."""
    delta = types.SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=None
    )
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], model="m", usage=None)


class _FakeStream:
    """Iterable stream driven by a script of ``(delay_s, chunk_or_None)``.

    A ``None`` chunk models a frame that carries no semantic delta — the SSE
    keepalive case. Iteration stops when the script runs out OR when
    ``stop`` is set, so a test that raises mid-stream never leaks the daemon
    worker thread.
    """

    def __init__(self, script, *, loop_last=False):
        self._script = list(script)
        self._loop_last = loop_last
        self.stop = threading.Event()
        self.frames_yielded = 0

    def __iter__(self):
        for delay, chunk in self._script:
            if self.stop.wait(delay):
                return
            self.frames_yielded += 1
            if chunk is not None:
                yield chunk
        while self._loop_last and not self.stop.wait(0.02):
            # Keepalive frames forever. These must be really YIELDED, not
            # merely counted: the consumer's queue then never empties, which
            # is what made an Empty-gated deadline check never run and let
            # the hang through. Yielding an empty-delta chunk is the shape a
            # real stalled provider produces.
            self.frames_yielded += 1
            yield _chunk()


class _Provider(OpenAICompatibleProvider):
    def __init__(self, stream):
        super().__init__(api_key="k", model="test-model")
        self._stream = stream
        self.attempts = 0

    def _create_client(self):
        provider = self

        def create(**_kwargs):
            provider.attempts += 1
            return provider._stream

        return types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
        )

    def get_available_models(self):
        return ["test-model"]


def _run(provider):
    return provider.chat_stream_response([{"role": "user", "content": "hi"}])


# --- must fire ------------------------------------------------------------


def test_keepalive_frames_without_deltas_still_time_out():
    """The actual bug: frames keep arriving, no content ever does.

    Counting bare chunk arrival as progress would make this hang forever,
    which is why progress is tracked on semantic deltas only.
    """
    stream = _FakeStream([], loop_last=True)
    provider = _Provider(stream)
    try:
        started = time.monotonic()
        with pytest.raises(StreamIdleTimeout):
            _run(provider)
        elapsed = time.monotonic() - started
    finally:
        stream.stop.set()

    assert stream.frames_yielded > 0, "the stream must really have been delivering"
    assert provider.attempts == 2, "an idle timeout should be re-issued once"
    assert elapsed < _DEADLINE_S * 6, f"took {elapsed:.2f}s — deadline not enforced"


def test_total_silence_times_out():
    stream = _FakeStream([(30.0, None)])
    provider = _Provider(stream)
    try:
        with pytest.raises(StreamIdleTimeout):
            _run(provider)
    finally:
        stream.stop.set()
    assert provider.attempts == 2


def test_stall_after_partial_content_is_not_retried():
    """Retry is skipped once output reached the caller — replaying it would
    duplicate the prefix. Same invariant the transport-drop lane enforces."""
    stream = _FakeStream([(0.0, _chunk(content="partial"))], loop_last=True)
    provider = _Provider(stream)
    seen: list[str] = []
    try:
        with pytest.raises(StreamIdleTimeout):
            provider.chat_stream_response(
                [{"role": "user", "content": "hi"}], on_text_chunk=seen.append
            )
    finally:
        stream.stop.set()
    assert seen == ["partial"]
    assert provider.attempts == 1, "must not replay a stream that already emitted"


# --- must NOT fire --------------------------------------------------------


def test_slow_but_productive_stream_is_never_interrupted():
    """The regression guard for "don't hurt other models".

    Deltas arrive with gaps just under the deadline, for longer in total
    than the deadline itself. A wall-clock cap would kill this; a
    content-progress deadline must not.
    """
    gap = _DEADLINE_S * 0.6
    stream = _FakeStream([
        (gap, _chunk(content="a")),
        (gap, _chunk(content="b")),
        (gap, _chunk(content="c")),
        (gap, _chunk(finish="stop")),
    ])
    provider = _Provider(stream)
    out = _run(provider)
    assert out.content == "abc"
    assert provider.attempts == 1, "a healthy stream must not be re-issued"


def test_reasoning_deltas_count_as_progress():
    """A model that thinks for a long time before speaking stays alive."""
    gap = _DEADLINE_S * 0.6
    stream = _FakeStream([
        (gap, _chunk(reasoning="thinking...")),
        (gap, _chunk(reasoning="still thinking...")),
        (gap, _chunk(content="answer")),
        (0.0, _chunk(finish="stop")),
    ])
    provider = _Provider(stream)
    out = _run(provider)
    assert out.content == "answer"
    assert out.reasoning_content == "thinking...still thinking..."
    assert provider.attempts == 1


def test_short_empty_response_completes_without_firing():
    """An empty completion that ENDS is a model quirk, not a hang — the
    query loop's empty-turn nudge owns that case, not this deadline."""
    stream = _FakeStream([(0.0, _chunk(finish="stop"))])
    provider = _Provider(stream)
    out = _run(provider)
    assert out.content == ""
    assert provider.attempts == 1
