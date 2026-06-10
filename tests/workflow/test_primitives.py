"""Tests for the pure parallel/pipeline argument helpers."""

from __future__ import annotations

import pytest

from src.workflow.errors import WorkflowError
from src.workflow.primitives import await_item, fit_args, run_stage


async def test_await_item_coroutine():
    async def coro():
        return 7

    assert await await_item(coro()) == 7


async def test_await_item_thunk_returning_coroutine():
    async def coro():
        return 9

    assert await await_item(lambda: coro()) == 9


async def test_await_item_thunk_returning_value():
    assert await await_item(lambda: 5) == 5


async def test_await_item_rejects_plain_value():
    with pytest.raises(WorkflowError):
        await await_item(42)


def test_fit_args_trims_to_declared_arity():
    assert fit_args(lambda a: a, (1, 2, 3)) == (1,)
    assert fit_args(lambda a, b: a, (1, 2, 3)) == (1, 2)
    assert fit_args(lambda a, b, c: a, (1, 2, 3)) == (1, 2, 3)


def test_fit_args_varargs_gets_everything():
    assert fit_args(lambda *xs: xs, (1, 2, 3)) == (1, 2, 3)


def test_fit_args_zero_arg_callable_gets_nothing():
    assert fit_args(lambda: 1, (1, 2, 3)) == ()


def test_fit_args_uninspectable_gets_everything():
    # Some builtins can't be signature-introspected; pass all args.
    assert fit_args(map, (1, 2, 3)) == (1, 2, 3)


async def test_run_stage_sync_and_async():
    assert await run_stage(lambda prev, item, index: prev + 1, 10, 99, 0) == 11

    async def astage(prev):
        return prev * 2

    assert await run_stage(astage, 4, 99, 0) == 8


async def test_run_stage_rejects_non_callable():
    with pytest.raises(WorkflowError):
        await run_stage("not callable", 1, 1, 0)
