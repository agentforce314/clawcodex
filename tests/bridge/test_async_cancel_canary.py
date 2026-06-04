"""Asyncio cancellation canary tests (Phase 0.3).

Validates the patterns that ``replBridge.ts`` and ``remoteBridgeCore.ts``
will need when ported to Python. Builds confidence that ``asyncio.Event`` +
``asyncio.CancelledError`` can stand in for TS's ``AbortSignal`` cleanly.

Three patterns covered:

1. ``asyncio.sleep`` interrupted by ``cancel()`` (basic shutdown unwind).
2. Combined cancel sources (substitute for TS ``combinedAbortSignal.ts``).
3. Generation-counter pattern for in-flight callback invalidation
   (substitute for TS ``TokenRefreshScheduler``'s generation counter at
   ``jwtUtils.ts:96-99``).
"""

from __future__ import annotations

import asyncio

import pytest


# -----------------------------------------------------------------------------
# Pattern 1: asyncio.sleep interrupted by cancel()
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_interrupted_by_cancel() -> None:
    """A task awaiting asyncio.sleep raises CancelledError on cancel()."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def sleeper() -> None:
        started.set()
        try:
            await asyncio.sleep(60)  # would otherwise block forever
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(sleeper())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_sleep_interrupted_by_event_via_wait_for() -> None:
    """Pattern: race asyncio.sleep against an event using asyncio.wait().

    This is the substitute for TS's ``setTimeout(resolve, ms)`` raced against
    ``signal.aborted`` — when the event fires first, the sleep is cancelled.
    """
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.create_task(stopper())

    sleep_task = asyncio.create_task(asyncio.sleep(60))
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait(
        {sleep_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
        timeout=2.0,
    )
    for t in pending:
        t.cancel()

    assert stop_task in done
    assert sleep_task in pending  # the sleep was cancelled, not completed
    assert stop.is_set()


# -----------------------------------------------------------------------------
# Pattern 2: Combined cancel sources (substitute for combinedAbortSignal.ts)
# -----------------------------------------------------------------------------


async def _combined_event_wait(*events: asyncio.Event) -> None:
    """Helper: wait until any of the provided events is set.

    Substitute for TS ``combinedAbortSignal([a, b, c])``. Cleans up its
    watcher tasks before returning so it doesn't leak listeners.
    """
    if any(e.is_set() for e in events):
        return
    tasks = [asyncio.create_task(e.wait()) for e in events]
    try:
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


@pytest.mark.asyncio
async def test_combined_signal_first_source_fires() -> None:
    """Combined-signal returns when the first source fires."""
    a = asyncio.Event()
    b = asyncio.Event()
    c = asyncio.Event()

    async def fire_a() -> None:
        await asyncio.sleep(0.01)
        a.set()

    asyncio.create_task(fire_a())
    await asyncio.wait_for(_combined_event_wait(a, b, c), timeout=1.0)
    assert a.is_set()
    assert not b.is_set()
    assert not c.is_set()


@pytest.mark.asyncio
async def test_combined_signal_short_circuit_when_pre_set() -> None:
    """If a source is already set on entry, return immediately."""
    a = asyncio.Event()
    b = asyncio.Event()
    a.set()
    # Must not block.
    await asyncio.wait_for(_combined_event_wait(a, b), timeout=0.1)


@pytest.mark.asyncio
async def test_combined_signal_cleans_up_pending_listeners() -> None:
    """When one source fires, the other watchers are cancelled (no leak)."""
    a = asyncio.Event()
    b = asyncio.Event()
    c = asyncio.Event()

    me = asyncio.current_task()
    tasks_before = {t for t in asyncio.all_tasks() if not t.done()} - {me}

    async def fire_a() -> None:
        await asyncio.sleep(0.01)
        a.set()

    asyncio.create_task(fire_a())
    await asyncio.wait_for(_combined_event_wait(a, b, c), timeout=1.0)
    await asyncio.sleep(0)  # let cancellations propagate

    tasks_after = {t for t in asyncio.all_tasks() if not t.done()} - {me}
    # Allow up to 1 residual (the fire_a task) but no growing leak.
    assert len(tasks_after) <= len(tasks_before) + 1


# -----------------------------------------------------------------------------
# Pattern 3: Generation counter for in-flight callback invalidation
# (substitute for TokenRefreshScheduler's generation pattern)
# -----------------------------------------------------------------------------


class GenerationGuard:
    """Pattern: serialize an async refresh callback by generation.

    Mirrors ``jwtUtils.ts:96-99`` ``nextGeneration(sessionId)`` and the
    check at line 178 (``generations.get(sessionId) !== gen``). When
    ``schedule()`` is called, the generation bumps; an in-flight async
    refresh that completes against a stale generation is dropped.
    """

    def __init__(self) -> None:
        self._gen = 0

    def next_gen(self) -> int:
        self._gen += 1
        return self._gen

    def is_current(self, gen: int) -> bool:
        return self._gen == gen


@pytest.mark.asyncio
async def test_generation_counter_invalidates_stale_callback() -> None:
    """An async callback that fires after the generation bumps is dropped."""
    guard = GenerationGuard()
    fired = []

    async def maybe_apply(gen: int, value: str) -> None:
        await asyncio.sleep(0.02)  # simulate I/O
        if not guard.is_current(gen):
            return  # stale — bail
        fired.append(value)

    g1 = guard.next_gen()
    t1 = asyncio.create_task(maybe_apply(g1, 'first'))

    # User cancels/reschedules — bump generation.
    g2 = guard.next_gen()
    t2 = asyncio.create_task(maybe_apply(g2, 'second'))

    await asyncio.gather(t1, t2)
    # Only the second (current-generation) callback should have fired.
    assert fired == ['second']


@pytest.mark.asyncio
async def test_generation_counter_allows_multiple_in_order_callbacks() -> None:
    """Sequential same-generation callbacks all fire (no false invalidation)."""
    guard = GenerationGuard()
    fired = []

    async def maybe_apply(gen: int, value: str) -> None:
        await asyncio.sleep(0)  # yield once
        if not guard.is_current(gen):
            return
        fired.append(value)

    gen = guard.next_gen()
    await asyncio.gather(
        maybe_apply(gen, 'a'),
        maybe_apply(gen, 'b'),
        maybe_apply(gen, 'c'),
    )
    assert sorted(fired) == ['a', 'b', 'c']


@pytest.mark.asyncio
async def test_generation_counter_resets_via_explicit_bump() -> None:
    """Cancelling all timers = bump all generations (jwtUtils.ts:243-247)."""
    guard = GenerationGuard()
    g1 = guard.next_gen()
    g2 = guard.next_gen()
    g3 = guard.next_gen()

    assert guard.is_current(g3)
    assert not guard.is_current(g1)
    assert not guard.is_current(g2)
