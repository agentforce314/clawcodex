"""Abort-aware wrappers for blocking ``urllib`` network calls.

ESC-cancel support for WebFetch/WebSearch (#276): ``urllib`` has no
cancellation primitive, so cancellation is built from two mechanisms:

- ``call_with_abort``: run the blocking call on a daemon worker thread and
  poll the abort signal from the caller; on abort the CALLER unblocks
  immediately (raises ``AbortError``) while the worker dies at its socket
  timeout. A late-arriving response is closed so the socket isn't leaked.
- ``abortable_read``: chunked body read with an abort listener that closes
  the response — closing the underlying socket unblocks a ``read()`` that
  is mid-await between bytes, which polling alone cannot do.
"""

from __future__ import annotations

import socket
import threading
from typing import Any, Callable, TypeVar

from .abort_controller import AbortError, AbortSignal

T = TypeVar("T")

_POLL_INTERVAL_S = 0.05
_READ_CHUNK_BYTES = 65536


def _safe_close(obj: Any) -> None:
    # ``close()`` alone does NOT interrupt a ``recv`` blocked on another
    # thread — the fd stays referenced by the in-flight read. Shut the
    # socket down first (http.client internals, best-effort) so the
    # blocked read raises immediately instead of waiting out the timeout.
    try:
        sock = obj.fp.raw._sock
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        obj.close()
    except Exception:
        pass


def call_with_abort(fn: Callable[[], T], abort_signal: AbortSignal | None) -> T:
    """Run blocking ``fn`` and return its result, raising ``AbortError``
    the moment ``abort_signal`` trips.

    On abort the worker thread is abandoned (it exits at its socket
    timeout, bounded by the caller's ``timeout=`` argument to urllib); if
    its result arrives after the abort it is closed and discarded.
    """
    if abort_signal is None:
        return fn()
    abort_signal.throw_if_aborted()

    result: list[T] = []
    error: list[BaseException] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            value = fn()
            if abort_signal.aborted:
                _safe_close(value)
            else:
                result.append(value)
        except BaseException as exc:  # noqa: BLE001 — relayed to the caller
            error.append(exc)
        finally:
            done.set()

    thread = threading.Thread(
        target=_worker, name="abortable-net-call", daemon=True
    )
    thread.start()
    while not done.wait(_POLL_INTERVAL_S):
        if abort_signal.aborted:
            raise AbortError(abort_signal.reason or "user_interrupt")
    if abort_signal.aborted:
        raise AbortError(abort_signal.reason or "user_interrupt")
    if error:
        raise error[0]
    return result[0]


def abortable_read(
    resp: Any, max_bytes: int, abort_signal: AbortSignal | None
) -> bytes:
    """Read up to ``max_bytes`` from ``resp`` in chunks, raising
    ``AbortError`` if ``abort_signal`` trips mid-read.

    An abort listener closes ``resp`` so a read blocked between bytes
    unblocks immediately instead of waiting out the socket timeout.
    """
    if abort_signal is None:
        return resp.read(max_bytes)
    abort_signal.throw_if_aborted()

    def _close_on_abort() -> None:
        _safe_close(resp)

    registered = abort_signal.add_listener(_close_on_abort, once=True)
    chunks: list[bytes] = []
    remaining = max_bytes
    try:
        while remaining > 0:
            abort_signal.throw_if_aborted()
            try:
                chunk = resp.read(min(_READ_CHUNK_BYTES, remaining))
            except Exception:
                if abort_signal.aborted:
                    raise AbortError(
                        abort_signal.reason or "user_interrupt"
                    ) from None
                raise
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        abort_signal.throw_if_aborted()
    finally:
        abort_signal.remove_listener(registered)
    return b"".join(chunks)
