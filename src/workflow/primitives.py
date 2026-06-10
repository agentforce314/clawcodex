"""Pure helpers for the ``parallel`` / ``pipeline`` primitives.

Kept separate from :mod:`src.workflow.runtime` so the argument-resolution rules
(coroutine vs. thunk; stage arity) are unit-testable in isolation. The
stateful primitive bodies live on ``WorkflowRun``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Sequence

from .errors import WorkflowError


async def await_item(item: Any) -> Any:
    """Resolve a ``parallel()`` item.

    Accepts a coroutine / awaitable (idiomatic ``agent(...)``) or a zero-arg
    callable returning one (a thunk, faithful to the JS ``() => agent(...)``
    surface).
    """
    if inspect.isawaitable(item):
        return await item
    if callable(item):
        result = item()
        return await result if inspect.isawaitable(result) else result
    raise WorkflowError("parallel() items must be coroutines or zero-arg callables")


def fit_args(fn: Callable[..., Any], args: Sequence[Any]) -> tuple[Any, ...]:
    """Trim ``args`` to the number of positional params ``fn`` declares.

    Mirrors JavaScript's "extra arguments are ignored": a ``pipeline`` stage
    written as ``lambda prev: ...`` receives only ``prev``, while
    ``def stage(prev, item, index)`` receives all three. Callables with
    ``*args`` (or whose signature can't be introspected, e.g. some builtins)
    receive everything.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return tuple(args)
    positional = 0
    for param in sig.parameters.values():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
            positional += 1
        elif param.kind == param.VAR_POSITIONAL:
            return tuple(args)
    # Trim to the declared arity (a 0-arg stage is called with no args, like JS).
    return tuple(args[:positional])


async def run_stage(stage: Callable[..., Any], prev: Any, item: Any, index: int) -> Any:
    """Invoke one ``pipeline`` stage with ``(prev, item, index)`` arity-fitted."""
    if not callable(stage):
        raise WorkflowError("pipeline() stages must be callables")
    result = stage(*fit_args(stage, (prev, item, index)))
    return await result if inspect.isawaitable(result) else result
