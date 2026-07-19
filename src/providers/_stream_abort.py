"""Shared abort-signal helper for provider streaming paths.

Three providers (Anthropic, Minimax, OpenAI-compatible) all need the
same pattern to make ESC interrupt a streaming HTTP read mid-flight:

1. Pre-call fast-path so a tripped signal at a turn boundary skips
   the API round-trip entirely.
2. Register a listener on the abort signal that calls
   ``stream.response.close()``. The close interrupts the SDK's
   blocking next-chunk read, which raises in the consumer thread.
3. Race-safe ordering: register-then-recheck closes the
   sub-microsecond window where ``AbortSignal._fire`` could snapshot
   the listener list and silently drop a freshly-appended listener.
4. Signal-state-authoritative exception translation: the SDK / httpx
   layer can raise several different exception classes depending on
   which syscall was in flight when the response closed, so
   ``signal.aborted`` is the only stable abort indicator.
5. Cleanup: detach the listener in ``finally`` so long-lived
   controllers (the REPL engine's, reused across many turns) don't
   accumulate dead listeners against gone streams.
6. Post-stream recheck: catch a signal that fires after the iterator
   exits naturally but before we return.

This module factors the bookkeeping into ``StreamAbortGuard`` so each
provider only owns the SDK-specific iteration shape (Anthropic's
``stream.text_stream``, OpenAI's bare ``for chunk in stream``,
Minimax's ``with``-block + ``get_final_message``). Adding a new
provider becomes: build a ``StreamAbortGuard(abort_signal)``, call
``raise_if_pre_aborted()`` before the API request, wrap the SDK's
stream object in ``with guard.attach(stream):``, and translate
exceptions via ``guard.reraise_if_aborted(exc)`` in the ``except``
block. The provider keeps full control over fallbacks (e.g.
Anthropic's ``StreamWatchdog`` non-streaming recovery) — the guard
just owns the listener lifecycle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.utils.abort_controller import AbortError

if TYPE_CHECKING:
    from src.utils.abort_controller import AbortSignal


__all__ = ["StreamAbortGuard"]


def _close_response_safely(stream: Any) -> None:
    """Best-effort force-close of ``stream.response`` — never raises.

    Both the Anthropic SDK (``client.messages.stream``'s
    ``MessageStream``) and the OpenAI SDK (``Stream`` from
    ``client.chat.completions.create(stream=True)``) expose the
    underlying httpx ``Response`` as ``stream.response``. Delegates to
    :func:`src.utils.stream_watchdog.force_close_response`, which
    shuts the underlying socket down BEFORE closing — a bare
    ``response.close()`` from the abort thread does not wake a
    consumer parked in ``ssl.read()``, so the interrupt would stop
    the chunks yet leave the turn hung. Shutdown+close is idempotent,
    so a double-close (listener fires AND the post-loop path also
    closes) is harmless.
    """
    from src.utils.stream_watchdog import force_close_response

    force_close_response(stream)


class StreamAbortGuard:
    """Provider-side coordinator for abort-aware streaming.

    A single instance is built per ``chat_stream_response`` call; the
    same instance handles the pre-call fast-path, listener lifecycle
    around the SDK iteration, and post-stream recheck. When
    ``abort_signal`` is ``None``, every method is a no-op — providers
    can use the guard unconditionally without branching on the
    presence of an abort signal.

    Usage pattern::

        guard = StreamAbortGuard(abort_signal)
        guard.raise_if_pre_aborted()  # before API request

        with client.messages.stream(...) as stream:  # SDK-specific
            with guard.attach(stream):
                try:
                    for chunk in stream.text_stream:  # SDK-specific
                        if guard.aborted:  # optional in-loop check
                            break
                        ...
                except Exception as exc:
                    guard.reraise_if_aborted(exc)
                    raise  # non-abort exception

        guard.raise_if_post_aborted()  # signal may have fired after
                                       # the with-block exited normally

    The guard does NOT own the SDK's stream lifecycle — the provider
    keeps its own ``with`` / ``try`` / ``finally`` around the stream
    object, so provider-specific recovery (e.g., the Anthropic
    watchdog's non-streaming fallback) and provider-specific cleanup
    (e.g., ``stream.get_final_message()``) stay where they belong.
    """

    __slots__ = ("_signal",)

    def __init__(self, abort_signal: "AbortSignal | None") -> None:
        self._signal = abort_signal

    @property
    def aborted(self) -> bool:
        """True when the signal has fired. ``False`` when no signal was provided.

        Cheaper than calling ``raise_if_aborted`` in a hot loop — the
        in-loop check inside ``for chunk in stream:`` uses this so it
        doesn't pay the exception construction cost on every chunk.
        """
        return self._signal is not None and self._signal.aborted

    def raise_if_pre_aborted(self) -> None:
        """Raise ``AbortError`` if the signal was already tripped at call entry.

        Called BEFORE the API round-trip so a signal that tripped at a
        turn boundary doesn't pay the request cost. Identical shape
        across every provider.
        """
        if self._signal is not None and self._signal.aborted:
            raise AbortError(self._signal.reason or "user_interrupt")

    def raise_if_post_aborted(self) -> None:
        """Raise ``AbortError`` if the signal tripped after stream exit.

        Catches the window between ``stream.__exit__`` (or the
        iterator's natural exhaustion) and the provider's return.
        Same shape as the pre-aborted check, called from a different
        boundary.
        """
        self.raise_if_pre_aborted()

    def reraise_if_aborted(self, original_exc: BaseException) -> None:
        """If the signal aborted, translate the SDK exception to ``AbortError``.

        The provider catches ``Exception`` around the streaming
        iterator and asks the guard whether the exception was caused
        by the user pressing ESC. We check the signal state (not the
        exception class) because different SDK versions raise
        different classes when the underlying response is closed
        mid-read — ``httpx.ReadError``, ``httpx.RemoteProtocolError``,
        ``OSError``, ``BrokenPipeError``, or wrapped variants
        depending on which syscall was in flight.

        If the signal isn't aborted (genuine network error, auth
        failure, etc.), this is a no-op so the provider's ``raise``
        statement runs and the real error propagates with its
        original class intact.
        """
        if self._signal is not None and self._signal.aborted:
            raise AbortError(self._signal.reason or "user_interrupt") from original_exc

    def attach(self, stream: Any) -> "_StreamAbortContext":
        """Register a close-on-abort listener for ``stream``'s lifetime.

        Returns a context manager. While active, a tripped signal
        synchronously calls ``stream.response.close()`` from whichever
        thread fires the abort (TUI keypress thread, headless SIGINT
        handler, etc.). On context exit the listener is detached so a
        long-lived ``AbortController`` doesn't accumulate dead
        listeners pointing at gone streams.

        When ``abort_signal`` is ``None`` this is a no-op context.
        """
        return _StreamAbortContext(self._signal, stream)


class _StreamAbortContext:
    """Context manager that owns one close-on-abort listener.

    Not part of the public API — callers construct via
    ``StreamAbortGuard.attach(stream)``.

    Registration ordering: register-then-recheck. The naive
    "if aborted: close else: add_listener" sequence has a
    sub-microsecond race where another thread can call ``_fire``
    between the ``aborted`` read and the ``add_listener`` append;
    ``_fire`` snapshots the listener list before iterating, so a
    listener appended after the snapshot is silently dropped.
    Register-then-recheck closes the gap: ``aborted`` is sticky-True
    after ``_fire`` runs, so the post-add read catches any concurrent
    fire, and the close callback is idempotent.
    """

    __slots__ = ("_signal", "_stream", "_listener")

    def __init__(self, signal: "AbortSignal | None", stream: Any) -> None:
        self._signal = signal
        self._stream = stream
        self._listener: Any = None

    def __enter__(self) -> "_StreamAbortContext":
        if self._signal is None:
            return self

        stream = self._stream

        def _close() -> None:
            _close_response_safely(stream)

        # Register, then re-check. See the docstring above for the
        # race analysis.
        self._listener = self._signal.add_listener(_close, once=True)
        if self._signal.aborted:
            _close()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Close-on-abort guarantee. The listener-firing path closes
        # the response synchronously from whichever thread tripped
        # the abort — but ``AbortSignal._fire`` snapshots the
        # listener list BEFORE iterating, so a narrow race exists:
        # if the consumer thread observes ``guard.aborted == True``,
        # breaks out, and runs this ``__exit__`` (which detaches the
        # listener) before the abort thread reaches the snapshot's
        # firing iteration, the listener is silently dropped and the
        # underlying httpx response leaks open. To close the gap we
        # do one more idempotent close here whenever ``aborted`` is
        # True at exit — covers both the in-loop-break path (OpenAI
        # provider) and any future provider that exits the attach
        # context after observing the abort without raising.
        if self._signal is not None and self._signal.aborted:
            _close_response_safely(self._stream)
        if self._listener is not None and self._signal is not None:
            try:
                self._signal.remove_listener(self._listener)
            except Exception:
                pass
        # Never suppress exceptions — the provider's surrounding
        # try/except is where exception translation happens via
        # ``StreamAbortGuard.reraise_if_aborted``.
        return False
