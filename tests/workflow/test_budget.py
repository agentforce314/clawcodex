"""Tests for the ``budget`` primitive."""

from __future__ import annotations

import math

import pytest

from src.workflow.budget import Budget
from src.workflow.errors import WorkflowBudgetExceeded


def test_no_target_is_unbounded():
    b = Budget(None)
    assert b.total is None
    assert b.remaining() == math.inf
    b.add(1_000_000)
    b.check()  # never raises


def test_spent_and_remaining():
    b = Budget(100)
    assert b.spent() == 0
    assert b.remaining() == 100
    b.add(60)
    assert b.spent() == 60
    assert b.remaining() == 40


def test_check_raises_at_ceiling():
    b = Budget(100)
    b.add(60)
    b.check()  # 60 < 100, ok
    b.add(50)  # now 110 >= 100
    with pytest.raises(WorkflowBudgetExceeded):
        b.check()


def test_remaining_floors_at_zero():
    b = Budget(50)
    b.add(80)
    assert b.remaining() == 0


def test_base_spent_is_shared_pool():
    external = {"v": 30}
    b = Budget(100, base_spent=lambda: external["v"])
    assert b.spent() == 30
    b.add(50)
    assert b.spent() == 80
    external["v"] = 60
    assert b.spent() == 110
    with pytest.raises(WorkflowBudgetExceeded):
        b.check()
