"""Agent name registry — Chunk F / WI-6.1 + Chunk-F-Phase-6 critic C1.

Critic C1 carried into Phase 7: the original Phase-6 implementation
used a bare ``dict[str, str]`` on ``ToolContext`` and did the
collision check + write across two unprotected statements in
``_launch_async_agent``. With Phase 7 introducing concurrent-spawn
patterns (coordinator mode launches workers in parallel), that
TOCTOU window lets a second spawn-with-same-name silently succeed
and steal the binding.

Fix shape (option (b) per the brief — SRP-clean): wrap the dict in a
small class with a single ``claim_or_raise`` method that does
check-and-claim under its own ``threading.RLock``. The registry is
the source of truth; ``_launch_async_agent`` no longer touches the
dict directly.

The class also exposes ``get`` / ``release`` / ``items`` /
``__contains__`` so existing readers (SendMessage routing, tests)
keep working with familiar shapes.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Iterator

from src.tasks_core import is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry


class AgentNameAlreadyClaimedError(Exception):
    """Raised by ``claim_or_raise`` when the requested name is already
    bound to a running task. Callers should treat this as "model picked
    a colliding name; tell them via SendMessage to continue the
    existing one or pick a different name."

    Subclassed from plain ``Exception`` (NOT ``ToolInputError``) so the
    registry stays decoupled from the tool layer; the agent tool's
    ``_launch_async_agent`` translates this into ``ToolInputError`` at
    the boundary.
    """

    def __init__(self, name: str, existing_agent_id: str) -> None:
        super().__init__(
            f"Agent name {name!r} is already registered to running task "
            f"{existing_agent_id}. Use SendMessage with to={name!r} to "
            f"continue, or pick a different name."
        )
        self.name = name
        self.existing_agent_id = existing_agent_id


class AgentNameRegistry:
    """Thread-safe map of human-readable agent names → agent ids.

    The single ``claim_or_raise`` method is the atomic primitive: it
    holds the registry's RLock across the read of the existing
    binding, the look-up of the existing task's terminal status, AND
    the write of the new binding. No TOCTOU window between the three.

    Other methods (``get``, ``__contains__``, ``items``, ``release``)
    are simple read/write operations that take the lock briefly.

    Per A6/C5 contract: callers must NOT hold the registry's RLock
    across an ``await``. ``claim_or_raise`` runs synchronously
    (``runtime_tasks.get`` and the Python attribute reads are all
    sync), so this isn't a problem in practice.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mapping: dict[str, str] = {}

    def claim_or_raise(
        self,
        name: str,
        agent_id: str,
        runtime_tasks: "RuntimeTaskRegistry",
    ) -> None:
        """Atomically claim ``name`` for ``agent_id``.

        Behavior:
        * Name unbound → bind to ``agent_id``.
        * Name bound to ``agent_id`` already → no-op (idempotent).
        * Name bound to a different agent_id whose task is **running** →
          ``AgentNameAlreadyClaimedError``.
        * Name bound to a different agent_id whose task is **terminal**
          (or evicted from the registry) → overwrite. Old terminal
          holders remain reachable via raw task_id + auto-resume
          (Chunk F / WI-7.4).

        The check + claim are atomic under the registry's RLock; two
        concurrent ``claim_or_raise`` calls with the same name will
        not both succeed.
        """
        with self._lock:
            existing = self._mapping.get(name)
            if existing == agent_id:
                return
            if existing is not None:
                existing_state = runtime_tasks.get(existing)
                if existing_state is not None and not is_terminal_task_status(
                    existing_state.status
                ):
                    raise AgentNameAlreadyClaimedError(name, existing)
            self._mapping[name] = agent_id

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._mapping.get(name)

    def release(self, name: str) -> bool:
        """Drop the binding for ``name``. Returns True if a binding
        was removed."""
        with self._lock:
            return self._mapping.pop(name, None) is not None

    def items(self) -> list[tuple[str, str]]:
        """Snapshot of every (name, agent_id) pair. Returns a list, not
        a live view — concurrent writers can't corrupt iteration."""
        with self._lock:
            return list(self._mapping.items())

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._mapping

    def __len__(self) -> int:
        with self._lock:
            return len(self._mapping)


__all__ = [
    "AgentNameAlreadyClaimedError",
    "AgentNameRegistry",
]
