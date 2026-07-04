"""Runtime task registry + per-type Task interface — Chunk B / WI-1.2.

Mirrors the dispatch shape of ``typescript/src/tasks.ts`` and ``Task.ts``'s
minimal ``Task`` interface (``{name, type, kill}``), translated to Python's
type system. The registry is the typed substitute for the TS pattern of
storing TaskState records on ``AppState.tasks: Record<string, TaskState>``.

Concurrency model — assumption A6 / concern C5 (both critic-confirmed)
=======================================================================

The registry guards its internal dict with a single ``threading.RLock``.
Async agents run on the asyncio loop in the main thread; background-bash
runs Popen reapers on worker threads. The lock crosses the boundary cheaply
because writes are short.

**Hard contract on ``update``:** the mutator MUST be a synchronous pure
function. Yielding under the RLock would deadlock the asyncio scheduler
against the bash worker threads also contending for the lock. Enforced by:

1. Docstring (here).
2. Runtime assert: ``inspect.iscoroutinefunction`` check rejects coroutine
   functions before acquiring the lock.

A future iteration may relax to fine-grained per-entry locking if profiling
shows contention, but for now the single lock is adequate (gap analysis
predicted tens of writes/sec at peak) and simpler to reason about.
"""
from __future__ import annotations

import inspect
import threading
from typing import Callable, Iterator, Protocol, TYPE_CHECKING

from .tasks_core import TaskStateBase, TaskType

if TYPE_CHECKING:
    pass  # No external types referenced beyond the core ones.


# ---------------------------------------------------------------------------
# Task interface (Protocol) — minimal kill-only contract per chapter §2.1
# ---------------------------------------------------------------------------


class Task(Protocol):
    """Minimal per-type Task interface.

    Mirrors ``typescript/src/Task.ts:72-76`` after the TS team subtracted
    ``spawn``/``render`` (those were never called polymorphically). Each
    concrete Task implementation owns spawn logic in its own module; only
    ``kill`` benefits from polymorphism, so it is the only required method.

    The ``async`` signature lets implementations (e.g.
    ``InProcessTeammateTask`` in Phase 6) await mailbox writes during kill
    without changing the Protocol. Sync implementations can return
    immediately — Python treats them as one-step coroutines once awaited.
    """

    name: str
    type: TaskType

    async def kill(
        self, task_id: str, registry: "RuntimeTaskRegistry"
    ) -> None: ...  # pragma: no cover (Protocol body)


# ---------------------------------------------------------------------------
# RuntimeTaskRegistry — typed map under a single RLock
# ---------------------------------------------------------------------------


class RuntimeTaskRegistry:
    """Thread-safe map of ``task_id`` to ``TaskStateBase`` (or subclass).

    Substitute for the TS ``AppState.tasks: Record<string, TaskState>``
    pattern. ``update(task_id, mutator)`` is the immutable-update analogue
    of the TS ``setAppState(prev => ...)`` idiom.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskStateBase] = {}

    # -- read paths -------------------------------------------------------

    def get(self, task_id: str) -> TaskStateBase | None:
        """Return the task with the given id, or ``None`` if absent."""
        with self._lock:
            return self._tasks.get(task_id)

    def all(self) -> list[TaskStateBase]:
        """Snapshot of every registered task. Returns a list, not a view —
        callers iterating outside the lock should not see in-flight writes."""
        with self._lock:
            return list(self._tasks.values())

    def by_type(self, task_type: TaskType) -> list[TaskStateBase]:
        """Snapshot filtered to a single TaskType — used by deprecated
        compatibility views (e.g. ``ToolContext.background_bash_tasks``)."""
        with self._lock:
            return [t for t in self._tasks.values() if t.type == task_type]

    def __contains__(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._tasks

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __iter__(self) -> Iterator[TaskStateBase]:
        # Iterate over the snapshot, not the live dict, to avoid
        # RuntimeError if writers add entries during iteration.
        return iter(self.all())

    # -- write paths ------------------------------------------------------

    def upsert(self, task: TaskStateBase) -> None:
        """Insert or replace the entry for ``task.id``."""
        with self._lock:
            self._tasks[task.id] = task

    def remove(self, task_id: str) -> bool:
        """Drop the entry; returns True iff something was removed."""
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def update(
        self,
        task_id: str,
        mutator: Callable[[TaskStateBase], TaskStateBase],
    ) -> bool:
        """Atomically replace ``self._tasks[task_id]`` with ``mutator(prev)``.

        Returns True if the mutation was applied (entry exists), False if no
        such entry. The mutator runs under the registry's RLock.

        **Contract — see module docstring (assumption A6 / concern C5):**
        the mutator MUST be a synchronous pure function. Awaiting under
        the lock would deadlock asyncio against bash worker threads.
        Enforced before lock acquisition:
        """
        if inspect.iscoroutinefunction(mutator):
            raise TypeError(
                "RuntimeTaskRegistry.update mutator must be a sync function — "
                "never `async def`. Awaiting under the registry RLock would "
                "deadlock the asyncio scheduler against bash worker threads. "
                f"Got coroutine function: {mutator!r}"
            )
        with self._lock:
            prev = self._tasks.get(task_id)
            if prev is None:
                return False
            new_state = mutator(prev)
            # Belt-and-braces: catch a mutator that returned a coroutine
            # (i.e. forgot to ``await`` and let the function be sync but
            # accidentally returned ``some_coroutine_function()``).
            if inspect.iscoroutine(new_state):
                # Surface the bug; do NOT install the bad value.
                # Cleanup the dangling coroutine to silence the warning.
                new_state.close()
                raise TypeError(
                    "RuntimeTaskRegistry.update mutator returned a coroutine "
                    "instead of a TaskStateBase. The mutator must run "
                    "synchronously under the registry lock."
                )
            self._tasks[task_id] = new_state
            return True

    def clear(self) -> None:
        """Drop every entry. Test-helper; production paths should call
        ``remove(task_id)`` for each task being torn down so per-type kill
        hooks fire."""
        with self._lock:
            self._tasks.clear()


# ---------------------------------------------------------------------------
# Per-type Task registration — analogous to TS getAllTasks() / getTaskByType
# ---------------------------------------------------------------------------

# Tasks register themselves into this list at module-import time:
# LocalShellTask / LocalAgentTask / InProcessTeammateTask / LocalWorkflowTask
# (the last IS ported — see tasks/__init__.py). Still out of scope: the
# feature-gated / remote types — RemoteAgent (no remote-cloud dispatch),
# Monitor/monitor_mcp (MONITOR_TOOL-gated OFF even in TS), Dream (KAIROS OFF).
_REGISTERED_TASKS: list[Task] = []


def register_task(task: Task) -> None:
    """Add a Task implementation to the dispatch registry.

    Idempotent — re-registering the same name is a no-op. (Useful for
    test reloads.)
    """
    for existing in _REGISTERED_TASKS:
        if existing.name == task.name and existing.type == task.type:
            return
    _REGISTERED_TASKS.append(task)


def get_all_tasks() -> list[Task]:
    """Return every registered Task implementation.

    Mirrors ``typescript/src/tasks.ts:getAllTasks``. Order is registration
    order; not relied on by callers.
    """
    return list(_REGISTERED_TASKS)


def get_task_by_type(task_type: TaskType) -> Task | None:
    """Return the registered Task whose ``.type`` matches, or ``None``."""
    for task in _REGISTERED_TASKS:
        if task.type == task_type:
            return task
    return None


__all__ = [
    "Task",
    "RuntimeTaskRegistry",
    "register_task",
    "get_all_tasks",
    "get_task_by_type",
]
