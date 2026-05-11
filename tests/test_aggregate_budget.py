"""ch07 / Phase 2a.5: AggregateBudget invariant.

`dataclasses.replace(ctx)` (the orchestrator's per-tool copy pattern,
ch07/M6) must share the aggregate-budget counter with the parent. If a
future refactor reverts this to a plain scalar field, the 200K
per-message cap would be silently bypassed across concurrent batches.
This test pins the invariant directly so the breakage shows up here,
not two phases away as a regression in the cap tests.
"""
from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest

from src.tool_system.context import AggregateBudget, ToolContext


def test_aggregate_budget_default_factory_yields_zero_total() -> None:
    budget = AggregateBudget()
    assert budget.total == 0
    assert isinstance(budget.lock, type(threading.Lock()))


def test_dataclasses_replace_shares_aggregate_budget(tmp_path) -> None:
    parent = ToolContext(workspace_root=tmp_path)
    child = dataclasses.replace(parent)
    # Same reference — not a copy.
    assert child.aggregate_budget is parent.aggregate_budget
    # Writes through the property proxy propagate.
    child.tool_result_chars_so_far = 42
    assert parent.tool_result_chars_so_far == 42
    # Direct writes propagate too.
    parent.aggregate_budget.total = 100
    assert child.tool_result_chars_so_far == 100


def test_aggregate_lock_shared_across_replace(tmp_path) -> None:
    parent = ToolContext(workspace_root=tmp_path)
    child = dataclasses.replace(parent)
    assert child._aggregate_lock is parent._aggregate_lock


def test_property_setter_routes_to_budget(tmp_path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.tool_result_chars_so_far = 999
    assert ctx.aggregate_budget.total == 999
    ctx.tool_result_chars_so_far = 0  # reset path used at turn boundary
    assert ctx.aggregate_budget.total == 0
