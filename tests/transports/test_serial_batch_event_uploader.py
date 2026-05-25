"""Tests for ``src.transports.serial_batch_event_uploader``.

Strategy
--------
No external infrastructure — tests inject an async ``send`` callable
via the config that captures batches and can be made to raise.

Timing-dependent tests measure elapsed time via ``time.monotonic``
or use very short delays (~10ms) so the test runs in a reasonable
budget without monkeypatching the event loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from src.transports.serial_batch_event_uploader import (
    RetryableError,
    SerialBatchEventUploader,
    SerialBatchEventUploaderConfig,
)


def _make_config(
    send,
    *,
    max_batch_size: int = 10,
    max_queue_size: int = 100,
    base_delay_ms: float = 10.0,
    max_delay_ms: float = 100.0,
    jitter_ms: float = 5.0,
    max_batch_bytes: int | None = None,
    max_consecutive_failures: int | None = None,
    on_batch_dropped=None,
) -> SerialBatchEventUploaderConfig[dict[str, Any]]:
    """Helper — small delays so test budgets stay tight."""
    return SerialBatchEventUploaderConfig(
        max_batch_size=max_batch_size,
        max_queue_size=max_queue_size,
        send=send,
        base_delay_ms=base_delay_ms,
        max_delay_ms=max_delay_ms,
        jitter_ms=jitter_ms,
        max_batch_bytes=max_batch_bytes,
        max_consecutive_failures=max_consecutive_failures,
        on_batch_dropped=on_batch_dropped,
    )


async def _wait_for(predicate, timeout_s: float = 2.0, step_s: float = 0.005):
    """Poll a callable until truthy or timeout."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        await asyncio.sleep(step_s)
    return last


# ── Enqueue + batching ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_single_item_sends() -> None:
    sent: list[list[dict[str, Any]]] = []

    async def send(batch):
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(send))
    await u.enqueue({'id': 1})
    await u.flush()
    assert sent == [[{'id': 1}]]


@pytest.mark.asyncio
async def test_enqueue_batches_up_to_max_batch_size() -> None:
    sent: list[list[dict[str, Any]]] = []
    # Use an event to gate the first send so subsequent enqueues
    # pile up in pending before drain proceeds.
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(send, max_batch_size=3))
    # Enqueue 10 items individually — first send is blocked on gate,
    # so items 2-10 accumulate in pending.
    for i in range(10):
        await u.enqueue({'id': i})
    # Now release the gate and let the drain work through.
    gate.set()
    await u.flush()
    # 10 items, max_batch_size=3 → 4 send calls (3+3+3+1)
    assert len(sent) == 4
    assert [len(b) for b in sent] == [3, 3, 3, 1]
    # All items present, in order.
    assert [m['id'] for batch in sent for m in batch] == list(range(10))


@pytest.mark.asyncio
async def test_enqueue_respects_max_batch_bytes() -> None:
    """First item always goes in regardless of size; subsequent items
    only if cumulative JSON bytes stay under max_batch_bytes."""
    sent: list[list[dict[str, Any]]] = []
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    # Each item serializes to about 16 bytes ({"id": N}).
    # max_batch_bytes=50 → about 2-3 items per batch.
    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=10, max_batch_bytes=50,
    ))
    for i in range(6):
        await u.enqueue({'id': i})
    gate.set()
    await u.flush()
    # All 6 items dispatched, but in multiple batches under the byte cap.
    assert sum(len(b) for b in sent) == 6
    assert len(sent) >= 2, 'byte limit should split into multiple batches'
    for batch in sent:
        # Verify each batch's serialized size respects the rule —
        # either it's a single item (always allowed) or its serialized
        # bytes are within the cap.
        import json
        total = sum(len(json.dumps(m).encode('utf-8')) for m in batch)
        assert len(batch) == 1 or total <= 50


@pytest.mark.asyncio
async def test_enqueue_first_item_alone_when_larger_than_max_bytes() -> None:
    """Phase-14b CRITIC: explicit test for the "first item always goes
    alone if it would otherwise exceed max_batch_bytes" branch. Pack
    a 200-byte first item followed by tiny items into a 50-byte cap;
    the first item dispatches alone in its own batch."""
    sent: list[list[dict[str, Any]]] = []
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=10, max_batch_bytes=50,
    ))
    # ~200-byte payload — much larger than the 50-byte cap.
    big = {'payload': 'x' * 200}
    await u.enqueue([big, {'id': 'small-1'}, {'id': 'small-2'}])
    gate.set()
    await u.flush()
    # First batch is the big item alone.
    assert sent[0] == [big]
    # Small items follow in a subsequent batch.
    assert sum(len(b) for b in sent[1:]) == 2


@pytest.mark.asyncio
async def test_enqueue_drops_unserializable_items_in_place() -> None:
    """A poison item (e.g. set, lambda) at the head of the queue is
    dropped during ``_take_batch`` instead of poisoning subsequent
    flush calls. Only requires max_batch_bytes to be set (which
    triggers the byte-limited path that calls json.dumps)."""
    sent: list[list[Any]] = []

    async def send(batch):
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=10, max_batch_bytes=1000,
    ))
    # The set() item raises TypeError on json.dumps; should be dropped.
    poison: Any = {1, 2, 3}
    await u.enqueue([{'id': 1}, poison, {'id': 2}])
    await u.flush()
    # Poison item dropped — sent batches contain only the two dicts.
    flat = [m for batch in sent for m in batch]
    assert len(flat) == 2
    assert all('id' in m for m in flat)


@pytest.mark.asyncio
async def test_enqueue_accepts_array_or_single() -> None:
    sent: list[list[dict[str, Any]]] = []
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(send, max_batch_size=10))
    await u.enqueue({'id': 1})            # single
    await u.enqueue([{'id': 2}, {'id': 3}])  # list
    gate.set()
    await u.flush()
    flat = [m for batch in sent for m in batch]
    assert [m['id'] for m in flat] == [1, 2, 3]


# ── Backpressure ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_blocks_when_max_queue_size_exceeded() -> None:
    gate = asyncio.Event()
    sent: list[list[dict[str, Any]]] = []

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=1, max_queue_size=1,
    ))
    # enqueue {1}: 0+1<=1, pending=[{1}], kick drain.
    await u.enqueue({'id': 1})
    # Yield so drain can run, take [{1}], and block on the gate.
    # After this, pending=[].
    await asyncio.sleep(0.02)
    # enqueue {2}: 0+1<=1, pending=[{2}] (queue at capacity now).
    await u.enqueue({'id': 2})
    # enqueue {3}: 1+1>1 → blocks until drain frees space.
    third_done = asyncio.Event()

    async def third():
        await u.enqueue({'id': 3})
        third_done.set()

    third_task = asyncio.create_task(third())
    # Yield — third should still be waiting under backpressure (drain
    # is blocked on gate so pending stays at 1).
    await asyncio.sleep(0.05)
    assert not third_done.is_set(), (
        'third enqueue must block under backpressure'
    )
    # Release the gate so drain can consume → backpressure releases.
    gate.set()
    await asyncio.wait_for(third_done.wait(), timeout=1.0)
    await u.flush()
    await third_task
    flat = [m['id'] for batch in sent for m in batch]
    assert flat == [1, 2, 3]


@pytest.mark.asyncio
async def test_enqueue_after_drain_releases_multiple_waiters() -> None:
    """Multiple blocked enqueues all eventually wake + complete after
    drain processes pending. Each new waiter may still re-block once
    while the next pending item drains, so this is a "stress" check
    of the release loop, not a single-cycle assertion."""
    gate = asyncio.Event()
    sent: list[list[dict[str, Any]]] = []

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=10, max_queue_size=1,
    ))
    await u.enqueue({'id': 0})   # pending=[{0}]
    await asyncio.sleep(0.02)    # drain pops {0}, blocks on gate
    await u.enqueue({'id': 1})   # pending=[{1}] (at capacity)
    completed: list[int] = []

    async def add(i: int):
        await u.enqueue({'id': i})
        completed.append(i)

    waiters = [asyncio.create_task(add(i)) for i in range(2, 4)]
    await asyncio.sleep(0.05)
    # Both 2 and 3 should be blocked (queue full at 1, drain stuck on gate).
    assert completed == []
    gate.set()
    await asyncio.wait_for(asyncio.gather(*waiters), timeout=2.0)
    assert sorted(completed) == [2, 3]


# ── Flush ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flush_returns_immediately_when_empty() -> None:
    async def send(batch):
        raise AssertionError('should not be called')

    u = SerialBatchEventUploader(_make_config(send))
    # Pending is empty, not draining — flush returns immediately.
    start = time.monotonic()
    await u.flush()
    elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms < 50  # generous bound; should be near-zero


@pytest.mark.asyncio
async def test_flush_blocks_until_drained() -> None:
    gate = asyncio.Event()
    sent: list[list[dict[str, Any]]] = []

    async def send(batch):
        await gate.wait()
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(send, max_batch_size=10))
    for i in range(5):
        await u.enqueue({'id': i})
    flush_done = asyncio.Event()

    async def flush():
        await u.flush()
        flush_done.set()

    flush_task = asyncio.create_task(flush())
    await asyncio.sleep(0.05)
    assert not flush_done.is_set()
    gate.set()
    await asyncio.wait_for(flush_done.wait(), timeout=1.0)
    await flush_task
    assert sum(len(b) for b in sent) == 5


@pytest.mark.asyncio
async def test_flush_during_failure_retries_resolves_after_success() -> None:
    """Flush blocks across retry attempts; resolves when drain finally
    succeeds and pending is empty."""
    call_count = {'n': 0}

    async def send(batch):
        call_count['n'] += 1
        if call_count['n'] < 3:
            raise RuntimeError(f'transient #{call_count["n"]}')
        # 3rd call succeeds.

    u = SerialBatchEventUploader(_make_config(
        send, base_delay_ms=5.0, max_delay_ms=20.0, jitter_ms=2.0,
    ))
    await u.enqueue({'id': 1})
    # Flush awaits across the retry cycles.
    await asyncio.wait_for(u.flush(), timeout=2.0)
    assert call_count['n'] == 3


# ── Retry / backoff ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_retries_with_exponential_backoff() -> None:
    """Successive failures should produce increasing delays. Measured
    via gaps between send-call timestamps."""
    call_times_ms: list[float] = []
    fail_until = 4  # succeed on the 4th attempt

    async def send(batch):
        call_times_ms.append(time.monotonic() * 1000)
        if len(call_times_ms) < fail_until:
            raise RuntimeError('transient')

    u = SerialBatchEventUploader(_make_config(
        send,
        base_delay_ms=20.0,
        max_delay_ms=200.0,
        jitter_ms=0.0,  # zero jitter for deterministic measurement
    ))
    await u.enqueue({'id': 1})
    await u.flush()
    assert len(call_times_ms) == fail_until
    # Gap[i] = delay between attempt i and i+1.
    gaps = [
        call_times_ms[i+1] - call_times_ms[i]
        for i in range(len(call_times_ms) - 1)
    ]
    # base=20, attempts 1/2/3 → delays 20/40/80 ms (zero jitter).
    # Tolerate event-loop scheduling slop.
    assert gaps[0] >= 15.0, f'first retry gap too short: {gaps[0]}ms'
    assert gaps[1] >= 35.0, f'second retry gap too short: {gaps[1]}ms'
    assert gaps[2] >= 75.0, f'third retry gap too short: {gaps[2]}ms'
    # And monotonically non-decreasing (within scheduling noise).
    assert gaps[1] > gaps[0] - 10
    assert gaps[2] > gaps[1] - 10


@pytest.mark.asyncio
async def test_failure_retries_indefinitely_without_max_consecutive() -> None:
    """Without max_consecutive_failures, the uploader retries forever
    until close()."""
    call_count = {'n': 0}

    async def send(batch):
        call_count['n'] += 1
        raise RuntimeError('always fails')

    u = SerialBatchEventUploader(_make_config(
        send, base_delay_ms=5.0, max_delay_ms=10.0, jitter_ms=0.0,
    ))
    await u.enqueue({'id': 1})
    # Wait for several retry cycles.
    await asyncio.sleep(0.1)
    early_count = call_count['n']
    assert early_count >= 3, (
        'expected several retries within 100ms with 5ms base delay'
    )
    # Close stops the retry loop.
    u.close()
    await asyncio.sleep(0.05)
    final_count = call_count['n']
    # After close, no more sends should fire — allow ≤1 for an in-flight
    # call that started before close was observed.
    assert final_count - early_count <= 1


@pytest.mark.asyncio
async def test_max_consecutive_failures_drops_batch_and_advances() -> None:
    """After max_consecutive_failures fails for the same batch, drop
    it, increment dropped_batch_count, and advance to the next item."""
    sent: list[list[dict[str, Any]]] = []
    fail_count = {'n': 0}
    fail_for_first = True

    async def send(batch):
        # Only the FIRST batch fails — once it's dropped, subsequent
        # batches succeed cleanly.
        if fail_for_first and batch and batch[0].get('id') == 'poison':
            fail_count['n'] += 1
            raise RuntimeError(f'fail #{fail_count["n"]}')
        sent.append(batch)

    u = SerialBatchEventUploader(_make_config(
        send,
        max_batch_size=1,
        base_delay_ms=2.0,
        max_delay_ms=5.0,
        jitter_ms=0.0,
        max_consecutive_failures=3,
    ))
    # Poison item that always fails, followed by a normal item.
    await u.enqueue({'id': 'poison'})
    await u.enqueue({'id': 'ok'})
    await u.flush()
    # Poison batch was dropped after 3 failures.
    assert u.dropped_batch_count == 1
    assert fail_count['n'] == 3
    # 'ok' batch went through.
    assert sent == [[{'id': 'ok'}]]


@pytest.mark.asyncio
async def test_on_batch_dropped_callback_fires() -> None:
    dropped_calls: list[tuple[int, int]] = []

    def on_batch_dropped(batch_size: int, failures: int) -> None:
        dropped_calls.append((batch_size, failures))

    async def send(batch):
        raise RuntimeError('always')

    u = SerialBatchEventUploader(_make_config(
        send,
        max_batch_size=2,
        base_delay_ms=2.0,
        max_delay_ms=5.0,
        jitter_ms=0.0,
        max_consecutive_failures=2,
        on_batch_dropped=on_batch_dropped,
    ))
    await u.enqueue([{'id': 1}, {'id': 2}])
    await u.flush()
    assert dropped_calls == [(2, 2)]


@pytest.mark.asyncio
async def test_retryable_error_honors_retry_after_ms() -> None:
    """RetryableError with retry_after_ms uses that delay (clamped) +
    jitter, overriding the exponential schedule."""
    call_times_ms: list[float] = []

    async def send(batch):
        call_times_ms.append(time.monotonic() * 1000)
        if len(call_times_ms) == 1:
            raise RetryableError('rate-limit', retry_after_ms=50.0)
        # 2nd call succeeds.

    u = SerialBatchEventUploader(_make_config(
        send,
        base_delay_ms=5.0,    # exponential would give ~5ms
        max_delay_ms=100.0,
        jitter_ms=0.0,
    ))
    await u.enqueue({'id': 1})
    await u.flush()
    assert len(call_times_ms) == 2
    gap = call_times_ms[1] - call_times_ms[0]
    # Server hint dominates over exponential's ~5ms.
    assert gap >= 45.0, f'expected ~50ms gap from retry_after_ms, got {gap}ms'


@pytest.mark.asyncio
async def test_retryable_error_clamps_below_base_delay() -> None:
    """retry_after_ms smaller than base_delay_ms is clamped up."""
    call_times_ms: list[float] = []

    async def send(batch):
        call_times_ms.append(time.monotonic() * 1000)
        if len(call_times_ms) == 1:
            raise RetryableError('hint-too-small', retry_after_ms=1.0)

    u = SerialBatchEventUploader(_make_config(
        send,
        base_delay_ms=30.0,   # clamp floor
        max_delay_ms=100.0,
        jitter_ms=0.0,
    ))
    await u.enqueue({'id': 1})
    await u.flush()
    gap = call_times_ms[1] - call_times_ms[0]
    assert gap >= 25.0, f'expected clamp to ~30ms, got {gap}ms'


@pytest.mark.asyncio
async def test_retryable_error_clamps_above_max_delay() -> None:
    """retry_after_ms larger than max_delay_ms is clamped down."""
    call_times_ms: list[float] = []

    async def send(batch):
        call_times_ms.append(time.monotonic() * 1000)
        if len(call_times_ms) == 1:
            raise RetryableError('hint-too-big', retry_after_ms=999_999.0)

    u = SerialBatchEventUploader(_make_config(
        send,
        base_delay_ms=5.0,
        max_delay_ms=50.0,    # clamp ceiling
        jitter_ms=0.0,
    ))
    await u.enqueue({'id': 1})
    await u.flush()
    gap = call_times_ms[1] - call_times_ms[0]
    assert gap >= 40.0, f'expected ≥40ms, got {gap}ms'
    assert gap < 200.0, f'expected ≤ ~max_delay_ms (with slop), got {gap}ms'


# ── Close ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_drops_pending_and_pending_count_snapshots() -> None:
    """close() drops pending; pending_count returns the snapshot."""
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()

    u = SerialBatchEventUploader(_make_config(send, max_batch_size=1))
    for i in range(5):
        await u.enqueue({'id': i})
    # Yield once to let drain start and consume the first batch (it
    # then blocks on the gate). After this, pending depth is 4.
    await asyncio.sleep(0.02)
    snapshot_before_close = u.pending_count
    assert snapshot_before_close >= 4
    u.close()
    # After close, pending_count returns the snapshot count at close
    # time (not 0, even though the queue was cleared).
    assert u.pending_count == snapshot_before_close
    # Subsequent enqueue is a no-op.
    await u.enqueue({'id': 'late'})
    assert u.pending_count == snapshot_before_close
    gate.set()
    # Release the in-flight send so it can complete cleanly.
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_close_releases_blocked_backpressure_waiters() -> None:
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()

    u = SerialBatchEventUploader(_make_config(
        send, max_batch_size=1, max_queue_size=1,
    ))
    await u.enqueue({'id': 0})    # pending=[{0}]
    await asyncio.sleep(0.02)     # drain consumes [{0}], blocks on gate
    await u.enqueue({'id': 1})    # pending=[{1}] (at capacity)
    waiter_done = asyncio.Event()

    async def waiter():
        # 1+1>1 → blocks until either drain frees space or close()
        # releases the waiter.
        await u.enqueue({'id': 2})
        waiter_done.set()

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    assert not waiter_done.is_set()
    u.close()
    # Backpressure waiter should resolve immediately after close.
    await asyncio.wait_for(waiter_done.wait(), timeout=1.0)
    await waiter_task
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_close_releases_blocked_flush_waiters() -> None:
    gate = asyncio.Event()

    async def send(batch):
        await gate.wait()

    u = SerialBatchEventUploader(_make_config(send))
    await u.enqueue({'id': 1})
    flush_done = asyncio.Event()

    async def flusher():
        await u.flush()
        flush_done.set()

    flush_task = asyncio.create_task(flusher())
    await asyncio.sleep(0.05)
    assert not flush_done.is_set()
    u.close()
    # Flush waiter should resolve immediately on close.
    await asyncio.wait_for(flush_done.wait(), timeout=1.0)
    await flush_task
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_close_during_sleep_interrupts_backoff() -> None:
    """Drain is sleeping in a backoff cycle; close fires; drain exits
    cleanly without waiting out the sleep."""
    sent_count = {'n': 0}

    async def send(batch):
        sent_count['n'] += 1
        raise RuntimeError('forever')

    u = SerialBatchEventUploader(_make_config(
        send,
        base_delay_ms=500.0,   # long enough to test interruption
        max_delay_ms=1000.0,
        jitter_ms=0.0,
    ))
    await u.enqueue({'id': 1})
    # Wait for the first send to fire (failure → drain enters sleep).
    await _wait_for(lambda: sent_count['n'] >= 1, timeout_s=0.5)
    # Now close — should interrupt the in-flight 500ms sleep.
    start = time.monotonic()
    u.close()
    # Give the drain a brief tick to observe close + exit.
    await asyncio.sleep(0.05)
    elapsed_ms = (time.monotonic() - start) * 1000
    # If sleep weren't interrupted, we'd need 500ms+ here.
    assert elapsed_ms < 200, (
        f'close should interrupt backoff sleep, but {elapsed_ms}ms elapsed'
    )


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    async def send(batch):
        return

    u = SerialBatchEventUploader(_make_config(send))
    u.close()
    u.close()  # second call is no-op
    assert u.pending_count == 0


# ── Misc ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dropped_batch_count_monotonic() -> None:
    fail_first_two = {'n': 0}

    async def send(batch):
        fail_first_two['n'] += 1
        if fail_first_two['n'] <= 4:  # 2 batches × 2 failures each
            raise RuntimeError('fail')

    u = SerialBatchEventUploader(_make_config(
        send,
        max_batch_size=1,
        base_delay_ms=1.0,
        max_delay_ms=2.0,
        jitter_ms=0.0,
        max_consecutive_failures=2,
    ))
    await u.enqueue([{'id': 'a'}, {'id': 'b'}, {'id': 'c'}])
    await u.flush()
    # Two batches dropped (a and b), one delivered (c).
    assert u.dropped_batch_count == 2


@pytest.mark.asyncio
async def test_enqueue_empty_list_is_noop() -> None:
    called = {'n': 0}

    async def send(batch):
        called['n'] += 1

    u = SerialBatchEventUploader(_make_config(send))
    await u.enqueue([])
    await asyncio.sleep(0.05)
    assert called['n'] == 0
    assert u.pending_count == 0
