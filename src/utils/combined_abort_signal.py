"""Combine multiple ``AbortSignal`` sources into a single derived signal.

Ports ``typescript/src/utils/combinedAbortSignal.ts``.

Used by ``bridgeApi`` (and Phase 5 orchestrators) to race a long-running
HTTP call against caller cancellation + an optional per-call timeout. The
returned signal aborts when *any* of: (a) the primary signal aborts,
(b) the secondary signal aborts, or (c) the timeout elapses. Returns a
``cleanup()`` so the caller can detach listeners + clear the timer when
the operation completes normally — without cleanup, listeners accumulate
on long-lived parent signals (the streaming executor creates one child
per tool, etc.).

Wraps the existing ``src.utils.abort_controller`` primitives rather than
inventing a new one — that module's listener registration + once-fire
semantics + child-controller pattern are already established and tested
in the codebase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from src.utils.abort_controller import (
    AbortSignal,
    create_abort_controller,
)


@dataclass(frozen=True)
class CombinedAbortSignal:
    """A merged signal plus a cleanup function.

    Mirrors TS ``{ signal, cleanup }`` return shape on
    ``combinedAbortSignal.ts:18``. ``cleanup`` is idempotent — safe to
    call multiple times; subsequent calls are no-ops.
    """

    signal: AbortSignal
    cleanup: Callable[[], None]


def create_combined_abort_signal(
    primary: AbortSignal | None,
    *,
    secondary: AbortSignal | None = None,
    timeout_seconds: float | None = None,
) -> CombinedAbortSignal:
    """Create a combined signal that aborts on any input source.

    Mirrors TS ``createCombinedAbortSignal`` on
    ``combinedAbortSignal.ts:15-60``. Keyword-only opts to make call
    sites self-documenting (TS uses an options object; Python uses
    kw-only — matches the existing ``get_bridge_status`` style).

    **Note on the timeout parameter**: TS takes ``timeoutMs`` (ms);
    Python takes ``timeout_seconds`` to match Python convention
    (``asyncio.sleep``, ``httpx`` timeouts all use seconds). Callers
    porting from TS should divide by 1000 at the call site.

    **Note on the timeout reason**: TS sets the abort reason to a
    ``DOMException('The operation timed out.', 'TimeoutError')``; the
    Python port uses the literal string ``'timeout'``. Callers that
    pattern-match the reason should adjust.

    **Short-circuit**: if either input is already aborted on entry, the
    returned signal is pre-aborted with a no-op cleanup. The timeout
    is NOT armed in that case (matches TS lines 22-29).

    Single-thread / single-asyncio-loop use only — concurrent calls
    against the same parent signal are safe (each gets its own combined
    controller + listener handles) but the cleanup must run on the same
    loop that armed the timer.
    """
    combined = create_abort_controller()

    if primary is not None and primary.aborted:
        combined.abort(primary.reason)
        return CombinedAbortSignal(signal=combined.signal, cleanup=lambda: None)
    if secondary is not None and secondary.aborted:
        combined.abort(secondary.reason)
        return CombinedAbortSignal(signal=combined.signal, cleanup=lambda: None)

    # Mutable state captured by the closures below. ``cleaned`` makes the
    # cleanup idempotent; ``timer`` is the asyncio TimerHandle so we can
    # cancel it on early cleanup.
    state: dict[str, object] = {'cleaned': False, 'timer': None}

    primary_listener: Callable[[], None] | None = None
    secondary_listener: Callable[[], None] | None = None

    def cleanup() -> None:
        if state['cleaned']:
            return
        state['cleaned'] = True
        timer = state['timer']
        if timer is not None:
            assert isinstance(timer, asyncio.TimerHandle)
            timer.cancel()
            state['timer'] = None
        if primary is not None and primary_listener is not None:
            primary.remove_listener(primary_listener)
        if secondary is not None and secondary_listener is not None:
            secondary.remove_listener(secondary_listener)

    def abort_combined(reason: str | None) -> None:
        cleanup()
        combined.abort(reason)

    def abort_from_primary() -> None:
        abort_combined(primary.reason if primary is not None else None)

    def abort_from_secondary() -> None:
        abort_combined(secondary.reason if secondary is not None else None)

    def abort_from_timeout() -> None:
        abort_combined('timeout')

    if timeout_seconds is not None:
        # Defer scheduling to the running loop; raises RuntimeError if no
        # loop is running. Matches TS setTimeout semantics (the equivalent
        # call would only work in a JS event loop too).
        loop = asyncio.get_running_loop()
        state['timer'] = loop.call_later(timeout_seconds, abort_from_timeout)

    if primary is not None:
        primary_listener = primary.add_listener(abort_from_primary)
    if secondary is not None:
        secondary_listener = secondary.add_listener(abort_from_secondary)

    return CombinedAbortSignal(signal=combined.signal, cleanup=cleanup)


__all__ = [
    'CombinedAbortSignal',
    'create_combined_abort_signal',
]
