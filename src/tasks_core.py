"""Task state-machine core types — Chunk B / WI-1.1.

Mirrors ``typescript/src/Task.ts``. Owns the type definitions and the
ID-generation/terminal-status helpers that the rest of the orchestration
layer (registry, per-type tasks, TaskStop, SendMessage) builds on.

Per refactoring plan §5 / §20.4: this lives at module path ``src.tasks_core``
(NOT ``src/tasks/__init__.py``) so the package directory ``src/tasks/`` is
free for per-type submodules without circular-import gymnastics.

Hard contract reminder (assumption A6 / concern C5): the typed registry that
stores ``TaskStateBase`` subclasses is implemented in ``src.task_registry``;
its ``update`` mutator MUST be a synchronous pure function — never ``await``
inside it. See ``RuntimeTaskRegistry.update`` for enforcement.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Final, Literal, TypeAlias

# ---------------------------------------------------------------------------
# Discriminator unions
# ---------------------------------------------------------------------------

# Mirrors typescript/src/Task.ts:6-13. Six of the seven types are out-of-scope
# for the chapter-10 port (Remote/Workflow/Monitor/Dream stay declared so the
# discriminator is byte-aligned with TS, but they have no Task implementation
# until later phases / chapters).
TaskType: TypeAlias = Literal[
    "local_bash",
    "local_agent",
    "remote_agent",
    "in_process_teammate",
    "local_workflow",
    "monitor_mcp",
    "dream",
]

# Mirrors typescript/src/Task.ts:15-20. Per assumption A3 the Python literal is
# ``"killed"`` (NOT ``"cancelled"``) — see also TeammateStatus rename in Phase 6.
TaskStatus: TypeAlias = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "killed",
]


def is_terminal_task_status(status: TaskStatus) -> bool:
    """True when a task is in a terminal state and will not transition further.

    Mirrors ``typescript/src/Task.ts:27-29``. Used everywhere a caller needs
    to guard against injecting messages into dead tasks, evicting finished
    tasks from the registry, or running orphan-cleanup paths.
    """
    return status in ("completed", "failed", "killed")


# ---------------------------------------------------------------------------
# ID generation — prefixed, CSPRNG-backed
# ---------------------------------------------------------------------------

# Single-character prefix per type (mirrors TS Task.ts:79-87). ``b`` is kept
# for ``local_bash`` for back-compat with TS's KillShell heritage.
_TASK_ID_PREFIXES: Final[dict[TaskType, str]] = {
    "local_bash": "b",
    "local_agent": "a",
    "remote_agent": "r",
    "in_process_teammate": "t",
    "local_workflow": "w",
    "monitor_mcp": "m",
    "dream": "d",
}

# Case-insensitive-safe alphabet (digits + lowercase). 36^8 ≈ 2.8 trillion
# combinations — enough to resist brute-force symlink attacks against the
# task output files on disk. Mirrors TS Task.ts:96 / :98-105.
_TASK_ID_ALPHABET: Final[str] = "0123456789abcdefghijklmnopqrstuvwxyz"
_TASK_ID_BODY_LEN: Final[int] = 8


def generate_task_id(task_type: TaskType) -> str:
    """Generate a prefixed task id of the form ``<prefix><8 base36 chars>``.

    ``secrets.choice`` (CSPRNG) — NOT ``random.choice`` — to mirror TS's
    ``crypto.randomInt`` at ``typescript/src/Task.ts:102``. The chapter
    explains the rationale: 36^8 combinations resists brute-forcing of task
    output filenames written under predictable paths. ``random.choice`` is
    seeded from process state and would weaken that guarantee.
    """
    prefix = _TASK_ID_PREFIXES.get(task_type, "x")
    body = "".join(secrets.choice(_TASK_ID_ALPHABET) for _ in range(_TASK_ID_BODY_LEN))
    return prefix + body


# ---------------------------------------------------------------------------
# Base state shared by every concrete TaskState
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class TaskStateBase:
    """Fields every concrete task-state subclass carries.

    Mirrors ``typescript/src/Task.ts:45-57``. TS uses ``startTime``/``endTime``
    in milliseconds; Python convention here is float seconds (``time.time()``)
    for natural arithmetic. ``output_offset`` is the read cursor for
    incremental disk-output reads (Phase 2 transcript writer + Phase 3
    notification XML); kept here on the base because every type writes to a
    file, even if the path/format differs.

    ``notified`` is the duplicate-completion-message guard — once the parent
    has been told a task finished, the flag flips to True via the atomic
    check-and-set in WI-3.1's ``enqueue_agent_notification``.

    ``kw_only=True`` is set so subclasses can override fields like ``type``
    with a Literal default without hitting Python's "non-default argument
    follows default argument" rule. All call sites already pass everything
    by name (refactor plan WI-1.1 specifies keyword construction).
    """

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    start_time: float
    output_file: str
    output_offset: int = 0
    notified: bool = False
    tool_use_id: str | None = None
    end_time: float | None = None
    total_paused_seconds: float = 0.0
    # Per-type extension fields land in subclasses (LocalShellTaskState,
    # LocalAgentTaskState, InProcessTeammateTaskState).


def create_task_state_base(
    *,
    id: str,
    type: TaskType,
    description: str,
    output_file: str,
    tool_use_id: str | None = None,
    status: TaskStatus = "pending",
) -> TaskStateBase:
    """Convenience factory — fills ``start_time`` from ``time.time()``."""
    return TaskStateBase(
        id=id,
        type=type,
        status=status,
        description=description,
        start_time=time.time(),
        output_file=output_file,
        tool_use_id=tool_use_id,
    )


__all__ = [
    "TaskType",
    "TaskStatus",
    "TaskStateBase",
    "create_task_state_base",
    "generate_task_id",
    "is_terminal_task_status",
]
