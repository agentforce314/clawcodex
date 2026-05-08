"""``src/tasks`` package — per-type Task implementations.

Established in Chunk B / WI-1.0 to replace the flat ``src/tasks.py`` file
(which contained a broken self-import of a vapor ``PortingTask`` class —
verified during the implementation pass: nothing in the production tree
ever defined ``PortingTask`` and ``default_tasks`` was uncallable). The
chapter-10 refactoring plan §19 deletes both the old ``src/tasks.py`` and
the parallel broken stubs ``src/Task.py`` / ``src/task.py``.

The package itself is intentionally light — concrete per-type submodules
live alongside this ``__init__.py``:

* ``local_shell`` — Chunk B / WI-1.4 + WI-2.1.
* ``local_agent`` — Chunk B / WI-1.5 + Chunk C / WI-2.3 (full lifecycle).
* ``in_process_teammate`` — Chunk F / WI-6.2.
* ``progress`` — Chunk C / WI-2.4.
* ``stop_task`` — Chunk E / WI-5.1.

**N1 fold-in (Chunk C):** registration of per-type ``Task`` adapters
into ``src.task_registry`` is centralized here. A single ``import
src.tasks`` triggers every type's registration in one place; the
per-type modules no longer carry their own ``_register()`` side
effect. This sidesteps the prior "must remember to import the
submodule before lookup" trap.
"""
from __future__ import annotations

# Re-export the per-type ``Task`` adapters so ``from src.tasks import
# LocalShellTask`` works. Submodule imports are listed individually
# (rather than ``import *``) so removing a future task type is a
# localized edit.
from src.task_registry import register_task
from src.tasks.in_process_teammate import (
    InProcessTeammateTask,
    InProcessTeammateTaskState,
)
from src.tasks.local_agent import LocalAgentTask, LocalAgentTaskState
from src.tasks.local_shell import LocalShellTask, LocalShellTaskState

# N1 (Chunk C) — centralized registration. Idempotent (``register_task``
# rejects duplicates), so importing ``src.tasks`` multiple times is safe.
register_task(LocalShellTask())
register_task(LocalAgentTask())
register_task(InProcessTeammateTask())

__all__ = [
    "InProcessTeammateTask",
    "InProcessTeammateTaskState",
    "LocalAgentTask",
    "LocalAgentTaskState",
    "LocalShellTask",
    "LocalShellTaskState",
]
