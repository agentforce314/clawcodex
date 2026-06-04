"""Tests for ``src.bridge.flush_gate.FlushGate``."""

from __future__ import annotations

from src.bridge.flush_gate import FlushGate


def test_initial_state_inactive() -> None:
    g: FlushGate[int] = FlushGate()
    assert g.active is False
    assert g.pending_count == 0


def test_enqueue_when_inactive_returns_false() -> None:
    g: FlushGate[int] = FlushGate()
    assert g.enqueue(1) is False
    assert g.pending_count == 0


def test_enqueue_when_active_returns_true_and_queues() -> None:
    g: FlushGate[int] = FlushGate()
    g.start()
    assert g.active
    assert g.enqueue(1) is True
    assert g.enqueue(2, 3) is True
    assert g.pending_count == 3


def test_end_drains_in_arrival_order_and_clears_active() -> None:
    g: FlushGate[str] = FlushGate()
    g.start()
    g.enqueue('a')
    g.enqueue('b', 'c')
    drained = g.end()
    assert drained == ['a', 'b', 'c']
    assert g.active is False
    assert g.pending_count == 0
    # After end, enqueue is no longer active.
    assert g.enqueue('d') is False


def test_drop_returns_count_and_clears_pending() -> None:
    g: FlushGate[int] = FlushGate()
    g.start()
    g.enqueue(1, 2, 3)
    assert g.drop() == 3
    assert g.active is False
    assert g.pending_count == 0


def test_deactivate_clears_active_but_keeps_pending() -> None:
    """Used when transport is replaced — new transport's flush will drain."""
    g: FlushGate[int] = FlushGate()
    g.start()
    g.enqueue(1, 2)
    g.deactivate()
    assert g.active is False
    # pending items are NOT dropped — they survive the deactivate.
    assert g.pending_count == 2
    drained = g.end()
    assert drained == [1, 2]


def test_multiple_start_end_cycles() -> None:
    g: FlushGate[int] = FlushGate()
    for cycle in range(3):
        g.start()
        g.enqueue(cycle)
        assert g.end() == [cycle]
        assert g.active is False


def test_generic_type_preserved() -> None:
    """FlushGate is generic — items round-trip through with their type."""
    g: FlushGate[dict[str, int]] = FlushGate()
    g.start()
    item = {'a': 1, 'b': 2}
    g.enqueue(item)
    drained = g.end()
    assert drained == [item]
    assert drained[0] is item, 'items should not be copied'
