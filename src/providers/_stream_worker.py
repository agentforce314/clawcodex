"""Worker-thread + bounded-queue stream consumption shared by providers.

Extracted from ``openai_compatible.py`` (#279) so AnthropicProvider and
MinimaxProvider get the same ESC-unwind guarantees: the SDKs' sync
``httpx`` reads don't reliably honor a cross-thread ``response.close()``
behind buffering proxies (LiteLLM, corporate proxies, mitmproxy), so the
blocking iteration runs on a daemon worker thread and the calling thread
polls a bounded queue, re-checking the abort signal between ticks.

Guarantees (pinned by tests/test_openai_compat_abort_signal.py and the
provider-specific abort tests):

- ESC unblocks the caller within ~100 ms regardless of SDK behavior.
- Items received BEFORE the abort are still delivered to ``on_item``;
  nothing is delivered after (the worker stops enqueueing the moment
  the abort trips).
- The queue is bounded (#278): a proxy that keeps sending after ESC
  cannot grow memory; the worker stops READING within one put-poll.
- A consumer that dies for a non-abort reason (``on_item`` raising)
  releases the worker via ``consumer_gone`` instead of leaving it
  retrying a full queue forever.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from typing import Any, Callable, TypeVar

from ._stream_abort import StreamAbortGuard

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DONE = object()
_QUEUE_MAXSIZE = 64
_PUT_POLL_S = 0.25
_GET_POLL_S = 0.1

# emit(item) -> bool: False means "stop producing" (abort/consumer gone).
Emit = Callable[[Any], bool]


def run_stream_on_worker(
    produce: Callable[[Emit], T],
    on_item: Callable[[Any], None],
    guard: StreamAbortGuard,
    *,
    thread_name: str = "provider-stream",
) -> T | None:
    """Run ``produce(emit)`` on a daemon worker thread, delivering each
    emitted item to ``on_item`` on the calling thread.

    Returns ``produce``'s return value. Raises ``AbortError`` promptly
    when the guard's signal trips; re-raises ``produce``'s exception
    otherwise (translated through ``guard.reraise_if_aborted`` first, so
    an SDK error caused by the close-on-abort listener surfaces as
    ``AbortError`` with the original as its cause).

    ``produce`` must treat ``emit(...) is False`` as "stop now": the
    consumer is gone or the user aborted, and nothing further will be
    drained.
    """
    chunk_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
    consumer_gone = threading.Event()

    def _put_or_drop(item: Any) -> bool:
        while True:
            if guard.aborted or consumer_gone.is_set():
                return False
            try:
                chunk_queue.put(item, timeout=_PUT_POLL_S)
                return True
            except queue.Full:
                continue

    def _emit(item: Any) -> bool:
        return _put_or_drop(("item", item))

    def _worker() -> None:
        try:
            value = produce(_emit)
            _put_or_drop(("result", value))
        except BaseException as exc:  # noqa: BLE001 — relayed to the consumer
            if not _put_or_drop(("error", exc)):
                # Abort won the race against a genuine error; the
                # consumer raises AbortError, so keep the loser
                # visible somewhere.
                logger.debug("stream error dropped after abort", exc_info=exc)
        finally:
            _put_or_drop(_DONE)

    worker = threading.Thread(target=_worker, daemon=True, name=thread_name)

    outcome: tuple[str, Any] | None = None
    with contextlib.ExitStack() as consumer_scope:
        # Releases the worker (sets consumer_gone) no matter how the
        # consumer loop exits — abort, on_item error, or natural break —
        # so a blocked put never outlives its consumer.
        consumer_scope.callback(consumer_gone.set)
        worker.start()
        while True:
            try:
                msg = chunk_queue.get(timeout=_GET_POLL_S)
            except queue.Empty:
                # The 100 ms tick bounds how long the user waits between
                # ESC and the prompt returning, regardless of how slow /
                # blocked the underlying SDK iteration is.
                if guard.aborted:
                    guard.raise_if_post_aborted()
                continue

            if msg is _DONE:
                break
            kind, payload = msg
            if kind == "item":
                on_item(payload)
                # Check abort AFTER processing so any already-delivered
                # item is preserved; we just don't take the next one.
                if guard.aborted:
                    guard.raise_if_post_aborted()
                continue
            # "result" / "error" — terminal; the _DONE sentinel follows.
            outcome = (kind, payload)

    # Error outcomes FIRST (before the post-abort check) so a relayed
    # exception keeps its chain — an abort racing in after the relay
    # surfaces as ``AbortError from payload`` via reraise_if_aborted,
    # and a relayed KeyboardInterrupt/SystemExit is re-raised as-is so
    # the outer signal-handling story stays intact (pre-refactor
    # semantics: the error was raised at dequeue time).
    if outcome is not None and outcome[0] == "error":
        payload = outcome[1]
        if isinstance(payload, Exception):
            guard.reraise_if_aborted(payload)
        raise payload

    # The signal may have fired between the worker finishing and here.
    guard.raise_if_post_aborted()

    if outcome is not None:
        return outcome[1]
    # Defensive: an outcome-less _DONE should not occur (a dropped
    # result implies a tripped abort, which raises above).
    return None
