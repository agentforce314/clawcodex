"""Tests for ``src.transports.worker_state_uploader``.

Strategy
--------

Inject an async ``send`` callable that records payloads and can be
gated via an ``asyncio.Event`` to test the in-flight + pending
coalescing invariant. Backoff is exercised with small base/max delays
so the test budget stays tight (matches the convention in
``test_serial_batch_event_uploader.py``).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

from src.transports.worker_state_uploader import (
    WorkerStateUploader,
    WorkerStateUploaderConfig,
    _coalesce_patches,
)


def _config(send, *, base=5, max_=50, jitter=0):
    return WorkerStateUploaderConfig(
        send=send,
        base_delay_ms=base,
        max_delay_ms=max_,
        jitter_ms=jitter,
    )


# ---------------------------------------------------------------------------
# _coalesce_patches (free function)


def test_coalesce_top_level_keys_overwrite():
    base = {"a": 1, "b": 2}
    out = _coalesce_patches(base, {"b": 99, "c": 3})
    assert out == {"a": 1, "b": 99, "c": 3}


def test_coalesce_external_metadata_rfc7396_merge():
    base = {"external_metadata": {"x": 1, "y": 2}}
    out = _coalesce_patches(base, {"external_metadata": {"y": 99, "z": 3}})
    assert out == {"external_metadata": {"x": 1, "y": 99, "z": 3}}


def test_coalesce_internal_metadata_rfc7396_merge():
    base = {"internal_metadata": {"x": 1}}
    out = _coalesce_patches(base, {"internal_metadata": {"y": 2}})
    assert out == {"internal_metadata": {"x": 1, "y": 2}}


def test_coalesce_metadata_null_preserved_for_server_delete():
    base = {"external_metadata": {"x": 1, "y": 2}}
    out = _coalesce_patches(base, {"external_metadata": {"y": None}})
    # None preserved — server deletes the key.
    assert out == {"external_metadata": {"x": 1, "y": None}}


def test_coalesce_non_metadata_dict_does_not_merge():
    # Only external_metadata/internal_metadata get RFC 7396; other dicts
    # are overwritten wholesale.
    base = {"top": {"x": 1}}
    out = _coalesce_patches(base, {"top": {"y": 2}})
    assert out == {"top": {"y": 2}}


def test_coalesce_none_base_returns_overlay_copy():
    overlay = {"a": 1}
    out = _coalesce_patches(None, overlay)
    assert out == overlay
    # Defensive: not the same object.
    assert out is not overlay


def test_coalesce_does_not_mutate_inputs():
    base = {"external_metadata": {"x": 1}}
    overlay = {"external_metadata": {"y": 2}}
    base_snap = {"external_metadata": dict(base["external_metadata"])}
    overlay_snap = {"external_metadata": dict(overlay["external_metadata"])}
    _coalesce_patches(base, overlay)
    assert base == base_snap
    assert overlay == overlay_snap


# ---------------------------------------------------------------------------
# Single enqueue / drain


async def test_single_enqueue_calls_send_once():
    sent: list[dict[str, Any]] = []

    async def send(payload):
        sent.append(payload)
        return True

    u = WorkerStateUploader(_config(send))
    u.enqueue({"worker_status": "ready"})
    # Wait for the drain to finish.
    assert u._inflight is not None
    await u._inflight
    assert sent == [{"worker_status": "ready"}]


async def test_enqueue_while_inflight_only_one_pending_slot():
    """Verify the 1-in-flight + 1-pending invariant.

    Per TS contract: while a payload is in-flight, new enqueues go to a
    fresh `pending` slot — they are NOT merged into the in-flight
    payload (the in-flight payload is already committed to a network
    call). The pending slot itself coalesces if enqueued multiple times.

    After the in-flight completes, the pending slot is what drives the
    next send.
    """
    sent: list[dict[str, Any]] = []
    gate = asyncio.Event()

    async def send(payload):
        sent.append(payload)
        # First call blocks until released; subsequent calls return
        # immediately.
        if len(sent) == 1:
            await gate.wait()
        return True

    u = WorkerStateUploader(_config(send))
    u.enqueue({"external_metadata": {"x": 1}})
    # Let the first send start and block on the gate.
    await asyncio.sleep(0)
    # Two new patches arrive while #1 is in-flight. Both coalesce into
    # pending (not into the in-flight payload).
    u.enqueue({"external_metadata": {"y": 2}})
    u.enqueue({"external_metadata": {"z": 3}})
    # Release the gate; the iterative drain picks up the coalesced pending.
    gate.set()
    assert u._inflight is not None
    await u._inflight
    # Exactly two sends — first the original, second the coalesced pending.
    # The pending slot merged {y:2} and {z:3} (RFC 7396) but NOT {x:1}.
    assert sent == [
        {"external_metadata": {"x": 1}},
        {"external_metadata": {"y": 2, "z": 3}},
    ]


async def test_close_drops_pending_during_inflight():
    """In-flight task started; before drain absorbs the next iteration,
    close() drops pending and the iteration exits."""
    sent: list[dict[str, Any]] = []
    gate = asyncio.Event()

    async def send(payload):
        sent.append(payload)
        if len(sent) == 1:
            await gate.wait()
        return True

    u = WorkerStateUploader(_config(send))
    u.enqueue({"worker_status": "ready"})
    await asyncio.sleep(0)
    # Stage a pending patch, then close before releasing the gate.
    u.enqueue({"worker_status": "busy"})
    u.close()
    gate.set()
    assert u._inflight is not None
    await u._inflight
    # Only the first send actually happened — close dropped pending
    # before the iterative drain loop could pick it up.
    assert sent == [{"worker_status": "ready"}]


async def test_close_before_first_enqueue_blocks_subsequent_enqueue():
    sent: list[dict[str, Any]] = []

    async def send(payload):
        sent.append(payload)
        return True

    u = WorkerStateUploader(_config(send))
    u.close()
    u.enqueue({"x": 1})
    # Nothing was scheduled.
    assert u._inflight is None
    assert sent == []


# ---------------------------------------------------------------------------
# Send failure / retry


async def test_send_failure_triggers_retry(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)
    sent: list[dict[str, Any]] = []

    async def send(payload):
        sent.append(payload)
        return len(sent) >= 2  # first call fails, second succeeds

    u = WorkerStateUploader(_config(send, base=1, max_=10, jitter=0))
    u.enqueue({"a": 1})
    assert u._inflight is not None
    await u._inflight
    assert sent == [{"a": 1}, {"a": 1}]


async def test_send_exception_treated_as_failure(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)
    attempts = 0

    async def send(payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return True

    u = WorkerStateUploader(_config(send, base=1, max_=10, jitter=0))
    u.enqueue({"a": 1})
    assert u._inflight is not None
    await u._inflight
    assert attempts == 2


async def test_retry_absorbs_pending_patches(monkeypatch):
    """A new enqueue *during the backoff* gets coalesced into the
    next retry attempt.

    Per TS contract: ``Absorbs any pending patches before each retry``
    — distinct from the success path, where pending stays pending
    until the next drain iteration. This test stages the new patch
    while the in-flight send is *blocked before returning False*, so
    the absorb step sees the pending patch when the backoff sleep
    finishes.
    """
    monkeypatch.setattr(random, "random", lambda: 0.0)
    sent: list[dict[str, Any]] = []
    fail_gate = asyncio.Event()  # holds send #1 open before it returns False

    async def send(payload):
        sent.append(payload)
        if len(sent) == 1:
            # Block here BEFORE returning False so the test can stage a
            # pending patch that the absorb step (between sleep and
            # next send) will see.
            await fail_gate.wait()
            return False
        return True  # retry succeeds

    u = WorkerStateUploader(_config(send, base=1, max_=10, jitter=0))
    u.enqueue({"external_metadata": {"x": 1}})
    # Let the drain start and call send (which blocks on fail_gate).
    await asyncio.sleep(0)
    # Stage the pending patch BEFORE send returns False.
    u.enqueue({"external_metadata": {"y": 2}})
    # Now release send #1 → returns False → backoff (1ms) → absorb pending.
    fail_gate.set()
    assert u._inflight is not None
    await u._inflight
    # Send #2 saw the coalesced payload: original {x:1} + pending {y:2}.
    assert sent == [
        {"external_metadata": {"x": 1}},
        {"external_metadata": {"x": 1, "y": 2}},
    ]


async def test_close_stops_retry_loop(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)
    sent: list[dict[str, Any]] = []

    async def send(payload):
        sent.append(payload)
        return False  # always fails — would retry forever without close

    u = WorkerStateUploader(_config(send, base=1, max_=5, jitter=0))
    u.enqueue({"a": 1})
    # Let two attempts happen.
    await asyncio.sleep(0.02)
    u.close()
    assert u._inflight is not None
    await u._inflight
    # Some number of attempts happened, but the loop exited.
    assert len(sent) >= 1


# ---------------------------------------------------------------------------
# _retry_delay (math correctness)


def test_retry_delay_exponential_growth_then_clamp(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)
    u = WorkerStateUploader(_config(send=None, base=10, max_=100, jitter=0))  # type: ignore[arg-type]
    # base * 2^(n-1)
    assert u._retry_delay(1) == 10
    assert u._retry_delay(2) == 20
    assert u._retry_delay(3) == 40
    assert u._retry_delay(4) == 80
    # Clamp kicks in.
    assert u._retry_delay(5) == 100
    assert u._retry_delay(10) == 100


def test_retry_delay_clamp_then_jitter(monkeypatch):
    """Jitter is added AFTER clamp — so max delay + full jitter can
    exceed max_delay_ms. Mirrors TS behavior."""
    monkeypatch.setattr(random, "random", lambda: 1.0)  # max jitter
    u = WorkerStateUploader(_config(send=None, base=10, max_=100, jitter=50))  # type: ignore[arg-type]
    # Clamped exponential at attempt 10 is 100; jitter 50 added on top.
    assert u._retry_delay(10) == 150
