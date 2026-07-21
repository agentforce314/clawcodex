"""WI-5.2: streaming watchdog for the Anthropic SDK ``messages.stream``.

Mirrors TS ``services/api/claude.ts`` (``getStreamIdleTimeoutMs`` /
``abortTimedOutStream``; default 90 s via ``openaiShim.ts:140``).

**Ping-aware liveness (primary).** The Anthropic Python SDK drops the
``ping`` keepalive EVENTS the API sends during long internal work
(``_streaming.py``: ``if sse.event == "ping": continue``), so ``reset()``
— driven by the TYPED event stream — goes silent whenever the model spends
>90 s between typed events: prompt processing on a large context, or a gap
before/between content blocks under heavy extended thinking. That is the
root cause of 18/89 terminal-bench crashes (2026-07-19); a flat 90 s idle
timeout killed healthy-but-slow streams. But the pings are still BYTES on
the wire that ``httpx.Response.num_bytes_downloaded`` counts, so on each
deadline the watchdog checks byte progress and RE-ARMS instead of firing
when bytes advanced — it fires only after a true dead-air window (no bytes
at all), matching the ping-aware TS watchdog.

**Two-phase deadline.** The deadline values run on BOTH paths: on the
httpx path they set the cadence at which byte progress is checked (the
dead-air window that must elapse with zero bytes before firing); when the
byte counter is unavailable (mocked/non-httpx transport) they are the pure
time-based deadline. Either way the FIRST event gets a longer grace
(:func:`stream_first_event_timeout_seconds`, default 300 s, for prompt
processing) before the deadline tightens to the inter-event ``timeout_s``
(90 s). A genuinely byte-silent window still fires correctly at these
deadlines; a stream that keeps sending bytes (pings/data) re-arms
indefinitely, bounded in practice by the caller's own request/agent
timeout (and httpx's read timeout).

When a deadline lapses the watchdog closes the underlying HTTP response,
interrupting the sync iterator; the provider RETRIES THE STREAM (bounded by
:func:`stream_idle_max_attempts`), then raises :class:`StreamIdleTimeout`.
The TS reference never downgrades to a non-streaming request on idle
timeout — it aborts and lets the retry layer re-issue the streaming call —
and neither do we: a non-streaming re-issue of an agentic payload is
refused outright by the Anthropic Python SDK ("Streaming is required for
operations that may take longer than 10 minutes") whenever ``max_tokens``
is opus-class, which turned every watchdog fire into a fatal error.

**Why threading.Timer, not asyncio.** The Anthropic Python SDK's
``messages.stream()`` is a SYNCHRONOUS context manager (``with``, not
``async with``); ``stream.text_stream`` is a synchronous iterator.
``asyncio.wait_for`` requires the inner code path be awaitable — it
can't wrap a synchronous generator. Switching the whole provider chain
to ``AsyncAnthropic`` would cascade async into every call site of
``chat_stream_response`` (query.py, engine.py, the turn loop). The
plan WI-5.2 explicitly rejected that approach.

The threading.Timer pattern: schedule a deadline; reset on each chunk;
on timeout call ``stream.response.close()`` which causes the next
iterator pull to raise. The provider catches the raise and falls back.

**Known fragility (per Phase 2 critic R5).** If a future Anthropic SDK
version adds retry/reconnect logic between ``stream.response.close()``
and the next iterator pull, the close may be invisible — the iterator
would silently continue against a fresh connection. Mitigation: test
coverage exercises the timeout path against a synthetic stalled stream
and verifies the fallback fires. Re-audit the SDK version when bumping.
"""

from __future__ import annotations

import logging
import os
import socket as _socket
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_STREAM_IDLE_TIMEOUT_S",
    "DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S",
    "DEFAULT_STREAM_IDLE_MAX_ATTEMPTS",
    "StreamIdleTimeout",
    "stream_idle_timeout_seconds",
    "stream_first_event_timeout_seconds",
    "stream_idle_max_attempts",
    "StreamWatchdog",
    "force_close_response",
]


def force_close_response(stream: Any) -> None:
    """Force a peer thread blocked on ``stream``'s next-chunk read to unwind.

    ``response.close()`` alone is NOT enough: closing an httpx response
    from another thread while the consumer is parked inside
    ``ssl.read()``/``recv()`` does not wake the blocked syscall — the fd
    stays referenced by the in-flight read, so the consumer hangs until
    the server happens to drop the connection (observed live: an
    agent-server ``interrupt`` during an Anthropic stream stopped the
    deltas but the worker never raised, and even this watchdog's own
    timeout close could not rescue it; faulthandler showed the consumer
    in ``ssl.py:read`` minutes later). ``socket.shutdown(SHUT_RDWR)`` is
    the documented cross-thread way to interrupt a blocked read: the
    recv returns immediately and the SDK raises in the consumer thread.

    The raw socket rides httpcore's response extensions
    (``extensions["network_stream"].get_extra_info("socket")``) on both
    the Anthropic and OpenAI SDK responses (httpx under both). Shutdown
    first (wakes the reader), then ``close()`` (releases the
    connection). Both steps are best-effort and idempotent; this helper
    never raises — it runs on abort-listener and timer threads where an
    exception would be lost anyway.
    """
    try:
        response = getattr(stream, "response", None)
        if response is None:
            return
        try:
            extensions = getattr(response, "extensions", None) or {}
            network_stream = extensions.get("network_stream")
            sock = (
                network_stream.get_extra_info("socket")
                if network_stream is not None
                else None
            )
            if sock is not None:
                sock.shutdown(_socket.SHUT_RDWR)
        except Exception:
            # Already shut down / not a real socket / transport without
            # the extension — fall through to the plain close.
            pass
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


DEFAULT_STREAM_IDLE_TIMEOUT_S = 90.0


DEFAULT_STREAM_IDLE_MAX_ATTEMPTS = 3


def stream_idle_max_attempts() -> int:
    """Total streaming attempts per call before :class:`StreamIdleTimeout`.

    ``1 + CLAUDE_STREAM_IDLE_MAX_RETRIES`` (default 2 retries → 3
    attempts; malformed/negative values fall back). Retrying the STREAM is
    the TS-parity recovery for an idle timeout — the first attempt's
    prompt-processing typically warms the prompt cache, so a retry's
    time-to-first-event is far shorter than the original's.
    """
    raw = os.environ.get("CLAUDE_STREAM_IDLE_MAX_RETRIES", "").strip()
    if not raw:
        return DEFAULT_STREAM_IDLE_MAX_ATTEMPTS
    try:
        retries = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_IDLE_MAX_ATTEMPTS
    if retries < 0:
        return DEFAULT_STREAM_IDLE_MAX_ATTEMPTS
    return 1 + retries


def stream_idle_timeout_seconds() -> float:
    """Resolve the INTER-EVENT idle timeout from
    ``CLAUDE_STREAM_IDLE_TIMEOUT_MS``.

    Falls back to ``DEFAULT_STREAM_IDLE_TIMEOUT_S`` (90s) when the env var
    is unset or malformed. Mirrors TS ``getStreamIdleTimeoutMs``
    (openaiShim.ts:232-239, default openaiShim.ts:140). This is the gap
    allowed BETWEEN stream events once the stream has started producing;
    the FIRST event gets the longer :func:`stream_first_event_timeout_seconds`
    grace.
    """
    raw = os.environ.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "").strip()
    if not raw:
        return DEFAULT_STREAM_IDLE_TIMEOUT_S
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_STREAM_IDLE_TIMEOUT_S
    if ms <= 0:
        return DEFAULT_STREAM_IDLE_TIMEOUT_S
    return ms / 1000.0


# The first event on a large-context request arrives only after the API
# finishes PROMPT PROCESSING (time-to-first-event). In the agentic loop
# every turn re-sends the whole (growing) conversation, so every stream
# call pays this cost — and it scales toward minutes as the context
# approaches the model's 1M window. The Anthropic Python SDK drops the
# ``ping`` keepalive events the API sends during this window
# (``_streaming.py``: ``if sse.event == "ping": continue``), so a
# flat inter-event idle timeout on the TYPED event stream mistakes a
# healthy-but-processing request for a hung one. This grace covers the
# first-event wait; it is bounded below the non-streaming request ceiling
# (``_api_timeout_seconds``, 600s) so a genuinely dead socket is still
# caught. Root cause of the 18/89 terminal-bench crashes, 2026-07-19.
DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S = 300.0


def stream_first_event_timeout_seconds() -> float:
    """Resolve the FIRST-event (prompt-processing) grace from
    ``CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS``.

    Falls back to ``DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S`` (300s) when
    unset/malformed. Always at least the inter-event timeout — a smaller
    configured value would make the first event stricter than later ones,
    which is backwards.
    """
    inter = stream_idle_timeout_seconds()
    raw = os.environ.get("CLAUDE_STREAM_FIRST_EVENT_TIMEOUT_MS", "").strip()
    if not raw:
        return max(DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S, inter)
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return max(DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S, inter)
    if ms <= 0:
        return max(DEFAULT_STREAM_FIRST_EVENT_TIMEOUT_S, inter)
    return max(ms / 1000.0, inter)


class StreamIdleTimeout(Exception):
    """Raised when the stream idle watchdog fires.

    Internally the watchdog's ``close()`` interrupts the consumer
    iterator; the provider retries the STREAM up to
    :func:`stream_idle_max_attempts` times and raises this on
    exhaustion. Never recovered via a non-streaming re-issue (the SDK
    refuses those for opus-class ``max_tokens``).
    """


class StreamWatchdog:
    """Manage a per-stream idle deadline.

    Usage::

        watchdog = StreamWatchdog(stream, timeout_s=90.0)
        try:
            watchdog.arm()
            for chunk in stream.text_stream:
                watchdog.reset()  # got a chunk, push deadline back
                ...
        finally:
            watchdog.disarm()

    On timeout the watchdog's timer thread calls ``stream.response.close()``;
    the next ``stream.text_stream`` pull raises. The provider catches
    the raise and decides whether to fall back.
    """

    def __init__(
        self,
        stream: Any,
        *,
        timeout_s: float | None = None,
        first_event_timeout_s: float | None = None,
        request_id: str | None = None,
    ) -> None:
        self._stream = stream
        self._timeout_s = (
            timeout_s if timeout_s is not None else stream_idle_timeout_seconds()
        )
        # Two-phase (time-based fallback): the FIRST event gets a longer
        # grace (prompt processing / time-to-first-event), later events the
        # normal inter-event idle. ``reset()`` flips ``_seen_event`` on the
        # first call. NOTE: ``first_event_timeout_s`` is resolved
        # INDEPENDENTLY of ``timeout_s`` — a caller that passes only
        # ``timeout_s`` gets the env/default first-event grace (300s), not a
        # scaled ``timeout_s``. Tests that want a short first phase must pass
        # both (the ``max(...)`` floor only prevents first < inter).
        self._first_event_timeout_s = (
            first_event_timeout_s
            if first_event_timeout_s is not None
            else max(stream_first_event_timeout_seconds(), self._timeout_s)
        )
        self._seen_event = False
        self._request_id = request_id
        # Ping-aware liveness: raw bytes read off the HTTP response at the
        # moment the current deadline was set. The typed event stream drops
        # ``ping`` keepalives, but they are still bytes httpx counts, so byte
        # progress proves the stream is alive even when no typed event has
        # been yielded (the actual terminal-bench failure mode).
        self._last_bytes: int | None = None
        self._timer: threading.Timer | None = None
        # ch04 round-4 GAP D — half-time warning timer (TS claude.ts:1898,
        # :1919-1929 warns at timeout/2). Log-only; same reset/cancel
        # lifecycle and stale-timer race guards as the main deadline.
        self._warn_timer: threading.Timer | None = None
        # Event raised when the timer fires — consumer can check
        # ``watchdog.fired`` to distinguish a timeout from an SDK-side
        # error in the fallback decision.
        self._fired = threading.Event()
        self._lock = threading.Lock()

    @property
    def fired(self) -> bool:
        """True if the timeout fired (i.e., we triggered the stream close)."""
        return self._fired.is_set()

    def _read_bytes_downloaded(self) -> int | None:
        """Raw bytes read off the streaming HTTP response so far, or ``None``
        when the transport doesn't expose the counter (a mocked stream, a
        non-httpx transport). ``httpx.Response.num_bytes_downloaded``
        advances on every chunk the SDK reads — including the ``ping``
        keepalive lines it then drops — so this is the liveness signal the
        typed event stream can't provide."""
        response = getattr(self._stream, "response", None)
        n = getattr(response, "num_bytes_downloaded", None)
        return n if isinstance(n, int) else None

    def _arm_locked(self) -> None:
        self._cancel_locked()
        self._last_bytes = self._read_bytes_downloaded()
        self._timer = self._make_timer_locked()
        self._timer.start()
        self._warn_timer = self._make_warn_timer_locked()
        if self._warn_timer is not None:
            self._warn_timer.start()

    def arm(self) -> None:
        """Start the deadline. Idempotent: re-arming cancels the prior timer."""
        with self._lock:
            self._arm_locked()

    def reset(self) -> None:
        """Push the deadline forward — called on every stream event.

        The first call also ends the first-event grace: every deadline from
        here uses the (shorter) inter-event timeout.
        """
        if self._fired.is_set():
            return  # already timed out; reset is a no-op
        with self._lock:
            self._seen_event = True
            self._arm_locked()

    def disarm(self) -> None:
        """Cancel the timer (call after the stream completes normally)."""
        with self._lock:
            self._cancel_locked()

    def _effective_timeout_locked(self) -> float:
        """The deadline for the phase we're in: the first-event grace until
        the first event arrives, the inter-event idle afterward. Called
        under ``self._lock`` (from arm/reset)."""
        return self._timeout_s if self._seen_event else self._first_event_timeout_s

    def _make_timer_locked(self) -> threading.Timer:
        """Build a new Timer bound to this exact instance for stale-fire
        detection.

        Critic M2: ``Timer.cancel()`` is a no-op once the timer's worker
        thread has entered the callback. If ``reset()`` lands AFTER
        ``_on_timeout`` started but BEFORE it took the lock, the cancel
        does nothing and the callback proceeds to fire. To detect that
        race we capture the timer object in a closure and have the
        callback short-circuit if it isn't ``self._timer`` anymore.
        """
        timer: threading.Timer | None = None

        def _callback() -> None:
            self._on_timeout(timer)

        timer = threading.Timer(self._effective_timeout_locked(), _callback)
        timer.daemon = True
        return timer

    def _make_warn_timer_locked(self) -> threading.Timer | None:
        """Half-time warning timer (GAP D). Same stale-fire closure guard
        as the deadline timer; log-only, never touches the stream."""
        effective = self._effective_timeout_locked()
        if effective <= 0:
            return None
        timer: threading.Timer | None = None

        def _callback() -> None:
            with self._lock:
                if self._warn_timer is not timer or self._fired.is_set():
                    return
                self._warn_timer = None
            logger.warning(
                "stream idle for %.0fs (half of the %.0fs timeout)%s",
                effective / 2,
                effective,
                f" — request_id={self._request_id}" if self._request_id else "",
            )

        timer = threading.Timer(effective / 2, _callback)
        timer.daemon = True
        return timer

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None
        if self._warn_timer is not None:
            try:
                self._warn_timer.cancel()
            except Exception:
                pass
            self._warn_timer = None

    def _on_timeout(self, expected_timer: threading.Timer | None) -> None:
        """Timer callback: mark fired, then close the stream's response.

        The close causes the next iterator pull in the consumer thread
        to raise. We swallow all close-side exceptions — the consumer's
        catch is what matters.

        Critic M2: short-circuit if the firing timer isn't the active one
        (the consumer raced ``reset()`` past our cancel point).
        """
        with self._lock:
            if self._timer is not expected_timer:
                return
            if self._fired.is_set():
                return
            # Ping-aware: if raw bytes advanced since this deadline was set,
            # the stream is alive — keepalive pings (or data the typed
            # iterator hasn't surfaced) are flowing. Re-arm instead of
            # killing a healthy-but-slow stream. This is the fix for the
            # 90s-idle false positives on large-context agentic requests
            # where the SDK hides the pings.
            current = self._read_bytes_downloaded()
            if (
                current is not None
                and self._last_bytes is not None
                and current > self._last_bytes
            ):
                self._arm_locked()
                return
            self._fired.set()
            self._timer = None
            stream = self._stream
        # Close the response OUTSIDE the lock — close() may block on
        # network I/O and we shouldn't hold the lock during that.
        # force_close_response shuts the socket down first so the
        # consumer's blocked read actually returns (a bare close does
        # not wake it — see the helper's docstring).
        force_close_response(stream)
