"""WI-5.2: streaming watchdog for the Anthropic SDK ``messages.stream``.

Mirrors TS ``services/api/claude.ts`` (``getStreamIdleTimeoutMs`` /
``abortTimedOutStream``; default 90 s via ``openaiShim.ts:140``). When no
chunks arrive for ``timeout_s`` seconds the watchdog closes the underlying
HTTP response, which interrupts the sync iterator and lets the provider
RETRY THE STREAM (bounded by :func:`stream_idle_max_attempts`), then raise
:class:`StreamIdleTimeout`. The TS reference never downgrades to a
non-streaming request on idle timeout — it aborts and lets the retry layer
re-issue the streaming call — and neither do we: a non-streaming re-issue
of an agentic payload is refused outright by the Anthropic Python SDK
("Streaming is required for operations that may take longer than 10
minutes") whenever ``max_tokens`` is opus-class, which turned every
watchdog fire into a fatal error (observed live: 18/89 terminal-bench
trials, 2026-07-19).

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
    "DEFAULT_STREAM_IDLE_MAX_ATTEMPTS",
    "StreamIdleTimeout",
    "stream_idle_timeout_seconds",
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
    """Resolve the idle-timeout from ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` env var.

    Falls back to ``DEFAULT_STREAM_IDLE_TIMEOUT_S`` (90s) when the env var
    is unset or malformed. Mirrors TS ``getStreamIdleTimeoutMs``
    (openaiShim.ts:232-239, default openaiShim.ts:140).
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
        request_id: str | None = None,
    ) -> None:
        self._stream = stream
        self._timeout_s = (
            timeout_s if timeout_s is not None else stream_idle_timeout_seconds()
        )
        self._request_id = request_id
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

    def arm(self) -> None:
        """Start the deadline. Idempotent: re-arming cancels the prior timer."""
        with self._lock:
            self._cancel_locked()
            self._timer = self._make_timer_locked()
            self._timer.start()
            self._warn_timer = self._make_warn_timer_locked()
            if self._warn_timer is not None:
                self._warn_timer.start()

    def reset(self) -> None:
        """Push the deadline forward — called on every successful chunk."""
        if self._fired.is_set():
            return  # already timed out; reset is a no-op
        with self._lock:
            self._cancel_locked()
            self._timer = self._make_timer_locked()
            self._timer.start()
            self._warn_timer = self._make_warn_timer_locked()
            if self._warn_timer is not None:
                self._warn_timer.start()

    def disarm(self) -> None:
        """Cancel the timer (call after the stream completes normally)."""
        with self._lock:
            self._cancel_locked()

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

        timer = threading.Timer(self._timeout_s, _callback)
        timer.daemon = True
        return timer

    def _make_warn_timer_locked(self) -> threading.Timer | None:
        """Half-time warning timer (GAP D). Same stale-fire closure guard
        as the deadline timer; log-only, never touches the stream."""
        if self._timeout_s <= 0:
            return None
        timer: threading.Timer | None = None

        def _callback() -> None:
            with self._lock:
                if self._warn_timer is not timer or self._fired.is_set():
                    return
                self._warn_timer = None
            logger.warning(
                "stream idle for %.0fs (half of the %.0fs timeout)%s",
                self._timeout_s / 2,
                self._timeout_s,
                f" — request_id={self._request_id}" if self._request_id else "",
            )

        timer = threading.Timer(self._timeout_s / 2, _callback)
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
            self._fired.set()
            self._timer = None
            stream = self._stream
        # Close the response OUTSIDE the lock — close() may block on
        # network I/O and we shouldn't hold the lock during that.
        # force_close_response shuts the socket down first so the
        # consumer's blocked read actually returns (a bare close does
        # not wake it — see the helper's docstring).
        force_close_response(stream)
