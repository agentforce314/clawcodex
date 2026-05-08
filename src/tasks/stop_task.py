"""Typed stop-task dispatch — Chunk E / WI-5.1 + WI-5.2.

Mirrors the surface of ``typescript/src/tasks/stopTask.ts:38-100``: a
shared helper that looks up a task by id, validates state, dispatches
to the per-type ``Task.kill`` adapter, and reports a structured result
the tool layer can format. Three TS-canonical error codes
(``not_found``, ``not_running``, ``unsupported_type``) plus one
Python-specific extension (``kill_timeout``) — see ``StopTaskErrorCode``
docstring for the rationale.

This module exists so ``_task_stop_call`` in ``task_stop.py`` can shrink
to ~15 lines (input/output formatting only). The dispatch logic lives
here, where it can be unit-tested independently of the tool's argument
plumbing and where Phase 6's in-process-teammate ``kill`` slots in
without re-touching the tool body.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from src.task_registry import RuntimeTaskRegistry, get_task_by_type
from src.tasks_core import is_terminal_task_status

if TYPE_CHECKING:
    from src.tool_system.context import ToolContext


# The three TS-canonical codes mirror ``stopTask.ts:10-18``. The fourth
# is a Python-specific extension covering the case where the kill
# coroutine exceeds its 5s budget — TS's kill paths are sync and never
# hit this. Surfacing it as a distinct code (rather than collapsing
# into ``not_running`` or ``unsupported_type``) lets the M1 regression
# guard remain meaningful: a hung kill is structurally different from
# "task already finished" or "no impl registered."
StopTaskErrorCode = Literal[
    "not_found",
    "not_running",
    "unsupported_type",
    "kill_timeout",
]


@dataclass(frozen=True)
class StopTaskError:
    """Structured error payload — TS returns this on the result rather
    than raising; we follow the same return-value path so call sites
    don't need a try/except dance."""

    code: StopTaskErrorCode
    message: str


@dataclass(frozen=True)
class StopTaskResult:
    """Output of ``stop_task``. Frozen so callers can pass it around
    without worrying about downstream mutation.

    ``stopped`` is True iff the kill action ran to completion; on any
    error code (including ``not_running``) it's False. The chapter
    notes (and TS matches) that "I tried to stop an already-completed
    task" surfaces as ``is_error=True`` — model-friendly because it
    tells the agent the situation rather than silently succeeding.
    """

    stopped: bool
    task_id: str
    task_type: str | None = None
    error: StopTaskError | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


# Chapter-prescribed kill budget. Mirrors the previous Chunk-B bridge
# constant so the M1 regression test's 5s expectation stays valid.
_KILL_TIMEOUT_SECONDS: float = 5.0


async def stop_task(
    task_id: str,
    context: "ToolContext",
    *,
    reason: str = "",
) -> StopTaskResult:
    """Atomically stop a task by id with typed dispatch.

    Resolution order:

    1. **Typed runtime_tasks dispatch.** Look up ``task_id`` in
       ``context.runtime_tasks``; if the entry is in a terminal state,
       return ``not_running``; otherwise dispatch via
       ``get_task_by_type(state.type).kill(...)`` (bounded by the kill
       budget — see M1 in ``task_stop.py`` history).
    2. **Legacy ``task_manager`` fallback** — ``context.task_manager``
       holds threaded ``ManagedTask`` entries that pre-date the
       chapter-10 task system. We dispatch them via
       ``task_manager.stop()`` for back-compat. This is option (b)
       from the Chunk E brief: a special branch with a clear
       deletion plan rather than a coercion into ``LocalShellTaskState``
       (option (a) would synthesize fake ``command``/``cwd``/Popen
       fields, which is misleading for a generic-Thread entry). To
       be removed when ManagedTask is fully migrated to the typed
       registry — track in the Phase-11 follow-up backlog.
    3. **Not found** — ``task_id`` matches neither registry; return
       ``not_found``.

    ``reason`` is forwarded to the caller's result formatter via the
    tool layer; it does not affect dispatch.
    """
    # Branch 1 — typed runtime_tasks dispatch.
    runtime = context.runtime_tasks.get(task_id)
    if runtime is not None:
        if is_terminal_task_status(runtime.status):
            return StopTaskResult(
                stopped=False,
                task_id=task_id,
                task_type=runtime.type,
                error=StopTaskError(
                    code="not_running",
                    message=(
                        f"Task {task_id} is not running "
                        f"(status: {runtime.status})"
                    ),
                ),
            )

        impl = get_task_by_type(runtime.type)
        if impl is None:
            return StopTaskResult(
                stopped=False,
                task_id=task_id,
                task_type=runtime.type,
                error=StopTaskError(
                    code="unsupported_type",
                    message=f"Unsupported task type: {runtime.type}",
                ),
            )

        # Bound the kill at 5s so a hung adapter doesn't make the tool
        # look successful. Preserves the M1 regression guard (was in
        # ``task_stop.py``; moved here as part of the WI-5.1 hoist).
        try:
            await asyncio.wait_for(
                impl.kill(task_id, context.runtime_tasks),
                timeout=_KILL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return StopTaskResult(
                stopped=False,
                task_id=task_id,
                task_type=runtime.type,
                error=StopTaskError(
                    code="kill_timeout",
                    message=(
                        f"kill timed out after {_KILL_TIMEOUT_SECONDS:.0f}s"
                    ),
                ),
            )

        # For ``local_bash`` we additionally consult Popen.poll() to
        # report a real ``stopped`` boolean — matches the legacy
        # behavior the back-compat tests exercise. Other task types
        # report stopped=True once kill has been attempted (the
        # cooperative-cancellation pattern means the actual exit may
        # land asynchronously).
        stopped = True
        if runtime.type == "local_bash":
            from src.tasks.local_shell import LocalShellTaskState

            refreshed = context.runtime_tasks.get(task_id)
            if isinstance(refreshed, LocalShellTaskState):
                proc = refreshed.proc
                stopped = proc is not None and proc.poll() is not None
                if not stopped:
                    from src.tool_system.tools.bash.background import (
                        stop_background_bash,
                    )
                    stopped = stop_background_bash(context, task_id)

        return StopTaskResult(
            stopped=stopped,
            task_id=task_id,
            task_type=runtime.type,
        )

    # Branch 2 — legacy ``task_manager`` fallback.
    task_manager = getattr(context, "task_manager", None)
    if task_manager is not None and task_manager.get(task_id) is not None:
        stopped = task_manager.stop(task_id)
        return StopTaskResult(
            stopped=stopped,
            task_id=task_id,
            task_type="managed_thread",
        )

    # Branch 3 — not found.
    return StopTaskResult(
        stopped=False,
        task_id=task_id,
        error=StopTaskError(
            code="not_found",
            message=f"No task found with ID: {task_id}",
        ),
    )


__all__ = [
    "StopTaskErrorCode",
    "StopTaskError",
    "StopTaskResult",
    "stop_task",
]
