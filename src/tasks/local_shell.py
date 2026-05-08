"""``local_bash`` task type — runtime state and Task adapter.

Chapter-10 / Chunk B WI-1.4 + WI-2.1 land the typed state and the minimal
``Task`` adapter for background-bash. The actual spawn / output-streaming /
reaper logic continues to live in ``src/tool_system/tools/bash/background.py``
and is rebadged in this same chunk to write ``LocalShellTaskState`` into the
new ``RuntimeTaskRegistry`` instead of the legacy ``background_bash_tasks``
dict-of-dicts.

Mirrors ``typescript/src/tasks/LocalShellTask/LocalShellTask.tsx``'s state
shape (TS keeps spawn metadata + Popen handle + reaper bookkeeping on the
state record). The ``kill`` method here is the polymorphic dispatch target
for ``stop_task`` (Phase 5).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import IO, Any, Literal, TYPE_CHECKING

from src.tasks_core import TaskStateBase

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry


@dataclass(kw_only=True)
class LocalShellTaskState(TaskStateBase):
    """Runtime state for a background-bash task.

    Extension fields beyond ``TaskStateBase``:

    * ``command`` — the literal shell command the model requested.
    * ``cwd`` — the working directory (string for serializability; the
      spawner converts back to ``Path`` where needed).
    * ``pid`` — OS process id of the wrapped ``bash -lc`` invocation.
    * ``output_path`` — the on-disk capture file (combined stdout/stderr).
      ``output_file`` on the base carries the same string for chapter-10
      uniformity; ``output_path`` is kept as the bash-specific name to
      avoid breaking existing readers.
    * ``exit_code`` — populated by the reaper thread once the process exits;
      ``None`` while running.
    * ``finished_at`` — populated alongside ``exit_code``.
    * ``proc`` / ``handle`` — runtime-only handles. Underscore-prefixed in
      the legacy dict-of-dicts; here they're regular attributes guarded by
      ``field(repr=False)`` so they don't leak into snapshots / logs.
    """

    type: Literal["local_bash"] = "local_bash"  # type: ignore[assignment]
    command: str = ""
    cwd: str = ""
    pid: int | None = None
    output_path: str = ""
    exit_code: int | None = None
    finished_at: float | None = None
    proc: subprocess.Popen | None = field(default=None, repr=False, compare=False)
    handle: IO[bytes] | None = field(default=None, repr=False, compare=False)

    def derived_status(self) -> Literal["running", "completed", "failed"]:
        """Compute the legacy three-value status string used by the bash
        background reader. Independent of ``self.status`` (which uses the
        canonical 5-value chapter-10 vocabulary)."""
        rc = self.exit_code
        if rc is None:
            return "running"
        return "completed" if rc == 0 else "failed"

    def to_legacy_dict(self) -> dict[str, Any]:
        """Project back to the dict-of-dicts shape that the pre-Chunk-B
        readers used. Kept for back-compat during the migration cycle so
        the deprecated ``ToolContext.background_bash_tasks`` view exposes
        the historical key set unchanged.
        """
        return {
            "task_id": self.id,
            "command": self.command,
            "description": self.description,
            "cwd": self.cwd,
            "started_at": self.start_time,
            "output_path": self.output_path,
            "pid": self.pid,
            "_proc": self.proc,
            "_handle": self.handle,
            "exit_code": self.exit_code,
            "finished_at": self.finished_at,
        }


def is_local_shell_task(state: Any) -> bool:
    """Type guard. Tolerant of ``None`` and arbitrary objects so
    callers can chain it into discriminator branches."""
    return isinstance(state, LocalShellTaskState)


# ---------------------------------------------------------------------------
# Task adapter — polymorphic kill dispatch for Phase 5's ``stop_task``
# ---------------------------------------------------------------------------


class LocalShellTask:
    """Minimal ``Task`` adapter for ``local_bash`` entries.

    Chunk B / WI-2.1 is intentionally a one-method shim: ``kill`` translates
    a registry lookup into a SIGTERM (and, on follow-ups, a SIGKILL ladder).
    The heavy spawn/reap logic stays in ``tool_system/tools/bash/background``
    so the bash machinery isn't moved across chunks.
    """

    name: str = "LocalShellTask"
    type: Literal["local_bash"] = "local_bash"

    async def kill(
        self, task_id: str, registry: "RuntimeTaskRegistry"
    ) -> None:
        state = registry.get(task_id)
        if not is_local_shell_task(state):
            return
        assert isinstance(state, LocalShellTaskState)  # narrow for mypy
        proc = state.proc
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except (ProcessLookupError, PermissionError):
            return


# Per Chunk-C N1 fold-in: registration moved to
# ``src/tasks/__init__.py`` so a single ``import src.tasks`` triggers
# every type's registration. This module only declares the type +
# adapter; it no longer mutates the registry on import.

__all__ = [
    "LocalShellTaskState",
    "LocalShellTask",
    "is_local_shell_task",
]
