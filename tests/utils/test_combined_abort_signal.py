"""Tests for ``src.utils.combined_abort_signal``."""

from __future__ import annotations

import asyncio

import pytest

from src.utils.abort_controller import create_abort_controller
from src.utils.combined_abort_signal import (
    CombinedAbortSignal,
    create_combined_abort_signal,
)


def test_primary_abort_propagates() -> None:
    parent = create_abort_controller()
    combined = create_combined_abort_signal(parent.signal)
    assert not combined.signal.aborted
    parent.abort('parent')
    assert combined.signal.aborted
    assert combined.signal.reason == 'parent'
    combined.cleanup()


def test_secondary_abort_propagates() -> None:
    a = create_abort_controller()
    b = create_abort_controller()
    combined = create_combined_abort_signal(a.signal, secondary=b.signal)
    b.abort('secondary-source')
    assert combined.signal.aborted
    assert combined.signal.reason == 'secondary-source'
    combined.cleanup()


def test_either_input_aborts_returns_pre_aborted_with_noop_cleanup() -> None:
    """If primary is already aborted on entry, return pre-aborted signal."""
    a = create_abort_controller()
    a.abort('pre')
    combined = create_combined_abort_signal(a.signal)
    assert combined.signal.aborted
    assert combined.signal.reason == 'pre'
    # cleanup must be safe to call.
    combined.cleanup()
    combined.cleanup()  # idempotent


def test_secondary_pre_aborted_short_circuits() -> None:
    a = create_abort_controller()
    b = create_abort_controller()
    b.abort('secondary-pre')
    combined = create_combined_abort_signal(a.signal, secondary=b.signal)
    assert combined.signal.aborted
    assert combined.signal.reason == 'secondary-pre'


def test_no_inputs_returns_unaborted_signal() -> None:
    """All-None inputs are legal — caller may add the timeout only."""
    combined = create_combined_abort_signal(None)
    assert not combined.signal.aborted
    combined.cleanup()


def test_cleanup_removes_listener_from_parent() -> None:
    """After cleanup, aborting the parent must NOT fire the combined signal."""
    parent = create_abort_controller()
    combined = create_combined_abort_signal(parent.signal)
    combined.cleanup()
    parent.abort('after-cleanup')
    assert not combined.signal.aborted


def test_cleanup_is_idempotent() -> None:
    parent = create_abort_controller()
    combined = create_combined_abort_signal(parent.signal)
    combined.cleanup()
    combined.cleanup()
    combined.cleanup()  # no exception


@pytest.mark.asyncio
async def test_timeout_aborts_combined_signal() -> None:
    combined = create_combined_abort_signal(None, timeout_seconds=0.05)
    await asyncio.sleep(0.1)
    assert combined.signal.aborted
    assert combined.signal.reason == 'timeout'
    combined.cleanup()


@pytest.mark.asyncio
async def test_cleanup_cancels_timer() -> None:
    """If caller cleans up before the timeout fires, the signal stays alive."""
    combined = create_combined_abort_signal(None, timeout_seconds=10.0)
    combined.cleanup()
    await asyncio.sleep(0.02)
    assert not combined.signal.aborted


@pytest.mark.asyncio
async def test_primary_abort_cancels_pending_timer() -> None:
    """When the parent aborts first, the timer should be cleaned up."""
    parent = create_abort_controller()
    combined = create_combined_abort_signal(
        parent.signal, timeout_seconds=10.0
    )
    parent.abort('parent-wins')
    await asyncio.sleep(0.02)
    assert combined.signal.aborted
    assert combined.signal.reason == 'parent-wins'


@pytest.mark.asyncio
async def test_pre_aborted_short_circuit_does_not_arm_timer() -> None:
    """Short-circuit path: no timer is created, even if timeout is provided."""
    parent = create_abort_controller()
    parent.abort('pre')
    # Should not raise RuntimeError("no running event loop") even when
    # called without one — but we're in an asyncio context here so the
    # alternative path would not raise either; the real assertion is that
    # the timer doesn't fire and cleanup is a no-op.
    combined = create_combined_abort_signal(
        parent.signal, timeout_seconds=0.01
    )
    await asyncio.sleep(0.05)
    # Reason should still be 'pre', not 'timeout'.
    assert combined.signal.reason == 'pre'


def test_returns_combined_abort_signal_dataclass() -> None:
    parent = create_abort_controller()
    combined = create_combined_abort_signal(parent.signal)
    assert isinstance(combined, CombinedAbortSignal)
    assert hasattr(combined, 'signal')
    assert hasattr(combined, 'cleanup')
