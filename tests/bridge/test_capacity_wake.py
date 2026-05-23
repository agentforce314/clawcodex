"""Tests for ``src.bridge.capacity_wake``.

Covers the 5 categories from refactoring plan §2 Phase 0:
(a) wake during sleep aborts sleep
(b) outer abort propagates
(c) cleanup removes listeners (no leaked tasks)
(d) double-wake re-arms a fresh controller
(e) race between outer + capacity abort
"""

from __future__ import annotations

import asyncio

import pytest

from src.bridge.capacity_wake import CapacityWake, create_capacity_wake


@pytest.mark.asyncio
async def test_wake_during_sleep_aborts_sleep() -> None:
    """(a) Calling wake() while a signal-merged event is awaited fires it immediately."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig = cw.signal()
    assert not sig.event.is_set()

    async def waker() -> None:
        await asyncio.sleep(0.01)
        cw.wake()

    asyncio.create_task(waker())
    await asyncio.wait_for(sig.event.wait(), timeout=1.0)
    assert sig.event.is_set()
    sig.cleanup()


@pytest.mark.asyncio
async def test_outer_abort_propagates() -> None:
    """(b) Setting the outer abort signal fires the merged event."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig = cw.signal()
    assert not sig.event.is_set()

    outer.set()
    await asyncio.wait_for(sig.event.wait(), timeout=1.0)
    assert sig.event.is_set()
    sig.cleanup()


@pytest.mark.asyncio
async def test_cleanup_removes_listeners() -> None:
    """(c) cleanup() cancels the watcher tasks so they don't leak."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig = cw.signal()
    # Get all running tasks before cleanup; should include the two watchers.
    tasks_before = {t for t in asyncio.all_tasks() if not t.done()}
    # The current task is one of them; exclude it for a stable count.
    me = asyncio.current_task()
    watcher_count_before = len(tasks_before - {me})
    assert watcher_count_before >= 2

    sig.cleanup()
    # Give the event loop a tick to process the cancellations.
    await asyncio.sleep(0)
    tasks_after = {t for t in asyncio.all_tasks() if not t.done()}
    watcher_count_after = len(tasks_after - {me})
    assert watcher_count_after < watcher_count_before


@pytest.mark.asyncio
async def test_double_wake_rearms_fresh_controller() -> None:
    """(d) After wake(), a new signal() call returns a fresh, unset event.

    Mirrors TS lines 30-34: wake() aborts the previous controller and
    arms a new one. A subsequent signal() must reflect the new controller,
    not the previously-fired one.
    """
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig1 = cw.signal()
    cw.wake()
    # sig1 should now be set (its wake source fired).
    await asyncio.wait_for(sig1.event.wait(), timeout=1.0)
    sig1.cleanup()

    # A fresh signal() must not be pre-set (fresh wake event).
    sig2 = cw.signal()
    assert not sig2.event.is_set()

    # And the new signal() must still be wake-able.
    cw.wake()
    await asyncio.wait_for(sig2.event.wait(), timeout=1.0)
    assert sig2.event.is_set()
    sig2.cleanup()


@pytest.mark.asyncio
async def test_race_between_outer_and_capacity_abort() -> None:
    """(e) When both outer and wake fire concurrently, the merged event is set.

    Mirrors TS lines 39-42: the short-circuit checks both sources at signal
    creation time. We test the post-creation race too.
    """
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig = cw.signal()

    # Both fire "simultaneously".
    cw.wake()
    outer.set()

    await asyncio.wait_for(sig.event.wait(), timeout=1.0)
    assert sig.event.is_set()
    sig.cleanup()


@pytest.mark.asyncio
async def test_short_circuit_when_outer_already_set() -> None:
    """signal() returns a pre-set event when outer is already aborted.

    Mirrors TS lines 39-42 short-circuit path.
    """
    outer = asyncio.Event()
    outer.set()
    cw = create_capacity_wake(outer)

    sig = cw.signal()
    assert sig.event.is_set()
    # cleanup must be safe to call even on the short-circuit path.
    sig.cleanup()


@pytest.mark.asyncio
async def test_short_circuit_when_wake_already_fired_then_signal() -> None:
    """If wake() fires before any signal() call, the next signal() is NOT pre-set.

    Per TS semantics: wake() re-arms a fresh controller (line 32-33), so the
    previously-set wake event is discarded. A subsequent signal() ties to
    the *new* wake event, not the old one.
    """
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    cw.wake()  # Fires + re-arms.
    sig = cw.signal()
    assert not sig.event.is_set()
    sig.cleanup()


@pytest.mark.asyncio
async def test_outer_event_set_during_signal_wait() -> None:
    """Setting outer mid-wait triggers the merged event (regression case)."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)

    sig = cw.signal()

    async def aborter() -> None:
        await asyncio.sleep(0.01)
        outer.set()

    asyncio.create_task(aborter())
    await asyncio.wait_for(sig.event.wait(), timeout=1.0)
    assert sig.event.is_set()
    sig.cleanup()


@pytest.mark.asyncio
async def test_signal_returned_after_outer_aborts_is_pre_set() -> None:
    """A signal() call AFTER outer aborts must be pre-set with no leaked tasks."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)
    outer.set()

    me = asyncio.current_task()
    tasks_before = {t for t in asyncio.all_tasks() if not t.done()} - {me}

    sig = cw.signal()
    assert sig.event.is_set()
    sig.cleanup()

    tasks_after = {t for t in asyncio.all_tasks() if not t.done()} - {me}
    # No new watcher tasks were spawned on the short-circuit path.
    assert len(tasks_after) <= len(tasks_before)


def test_create_capacity_wake_returns_capacity_wake() -> None:
    """Factory returns a CapacityWake instance."""
    outer = asyncio.Event()
    cw = create_capacity_wake(outer)
    assert isinstance(cw, CapacityWake)
