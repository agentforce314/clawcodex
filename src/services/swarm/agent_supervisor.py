"""Session-scoped admission control and live-agent bookkeeping for subagents.

Clawcodex spawns subagents down two paths that never met: ``_run_sync_agent``
(foreground, returns inline) and ``_launch_async_agent`` (background, lands in
``runtime_tasks``). Only the background path was observable, so nothing in the
process knew how many agents were live, nothing bounded how many could be
started, and neither client could ask what was running.

This module is the missing shared authority. Both Agent-tool spawn paths call
:meth:`AgentSupervisor.admit` before running and :meth:`AgentSupervisor.release`
when the worker actually exits, so one session-owned object answers "what is
running right now", enforces capacity, and holds the abort handles that make
interruption work for foreground agents too.

Agent-tool workers, persistent teammates, and workflow workers all participate
in the same admission and interruption lifecycle.

Modelled on the OpenAI Codex ``AgentRegistry`` (``codex-rs/core/src/agent/
registry.rs``): ``reserve_spawn_slot`` rejects with ``AgentLimitReached`` past a
thread cap, and ``exceeds_thread_spawn_depth_limit`` bounds nesting. The two
limits are deliberately independent there, and stay independent here.

The wire shape of :meth:`snapshot` is the ``DelegationStatusResponse`` the TUI
already declared in ``ui-tui/src/gatewayTypes.ts`` — including
``max_concurrent_children`` and ``max_spawn_depth``, which is what the client
has been asking the backend for since before this existed.

Which client reads what: ``ui-web``'s agents panel renders ``active[]`` from
this snapshot. ``ui-tui``'s overlay consumes only the caps and ``paused`` — its
rows still come from the reconstructed ``agent_progress`` tree, so its pause and
kill controls are now live but its list is not yet served from here.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_MAX_CONCURRENT_CHILDREN",
    "DEFAULT_MAX_SPAWN_DEPTH",
    "AgentAdmissionError",
    "AgentSupervisor",
    "max_concurrent_children",
    "max_spawn_depth",
]

#: Default ceiling on agents live at once in one session.
#:
#: This is a runaway backstop, NOT a scheduling budget — deliberately set well
#: above any legitimate fan-out so that hitting it means something is wrong.
#:
#: It cannot be derived from the tool-use orchestrator's own bound
#: (``_DEFAULT_MAX_TOOL_USE_CONCURRENCY``, 10), because the two count different
#: things: a background agent holds its slot until its *worker* exits, which can
#: be many turns after the tool call returned. Three background agents from an
#: earlier turn plus a ten-wide foreground batch is 13 live agents from a
#: session the orchestrator never over-admitted. Coordinator mode makes that
#: routine — every coordinator spawn is forced async and its prompt actively
#: teaches fan-out ("Parallelism is your superpower").
#:
#: Raise (or lower, to actually schedule) via ``CLAWCODEX_MAX_CONCURRENT_AGENTS``.
DEFAULT_MAX_CONCURRENT_CHILDREN = 32

#: Default ceiling on spawn nesting. Depth 0 is a child of the root session.
#:
#: Defense in depth rather than the primary lever: ``AGENT_TOOL_NAME`` is in
#: ``ALL_AGENT_DISALLOWED_TOOLS``, so an ordinary subagent cannot spawn a
#: grandchild at all — and coordinator workers cannot either, since Agent is
#: also absent from ``ASYNC_AGENT_ALLOWED_TOOLS``. The one path that genuinely
#: bypasses that filter is the fork agent (``use_exact_tools=True`` copies the
#: parent's tool array verbatim, Agent included). Workflow workers also acquire a supervisor slot. Raise via ``CLAWCODEX_MAX_AGENT_DEPTH``.
DEFAULT_MAX_SPAWN_DEPTH = 3

_ENV_MAX_CONCURRENT = "CLAWCODEX_MAX_CONCURRENT_AGENTS"
_ENV_MAX_DEPTH = "CLAWCODEX_MAX_AGENT_DEPTH"


def _positive_int_env(name: str, fallback: int) -> int:
    """Read a positive-integer override, falling back on absent/garbage input."""
    override = os.environ.get(name, "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return fallback


def max_concurrent_children() -> int:
    """Resolve the live-agent cap. ``CLAWCODEX_MAX_CONCURRENT_AGENTS`` wins."""
    return _positive_int_env(_ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT_CHILDREN)


def max_spawn_depth() -> int:
    """Resolve the nesting cap. ``CLAWCODEX_MAX_AGENT_DEPTH`` wins."""
    return _positive_int_env(_ENV_MAX_DEPTH, DEFAULT_MAX_SPAWN_DEPTH)


class AgentAdmissionError(Exception):
    """A spawn was refused by the supervisor.

    Raised by :meth:`AgentSupervisor.admit`. The Agent tool translates this
    into a model-facing ``ToolResult`` error rather than letting it escape as a
    crash — the message is written to tell the model what to do next, because
    the model is the caller that has to react to it.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        #: Machine-readable discriminator: ``paused`` | ``depth`` | ``capacity``.
        self.reason = reason


@dataclass
class _LiveAgent:
    """One admitted agent. Mutable; guarded by the supervisor's lock."""

    subagent_id: str
    parent_id: str | None
    depth: int
    goal: str
    model: str | None
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    tool_count: int = 0
    ancestors: tuple[str, ...] = ()
    # The run's AbortController. ``query()`` polls ``signal.aborted`` at every
    # yield point, so calling ``.abort()`` on it halts a live run. Held here so
    # foreground agents — which never enter ``runtime_tasks`` and so have no
    # other abort handle anywhere — can be interrupted too.
    abort_controller: Any = field(default=None, repr=False)

    def to_wire(self) -> dict[str, Any]:
        """Project to one ``DelegationStatusResponse.active[]`` entry."""
        return {
            "subagent_id": self.subagent_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "goal": self.goal,
            "model": self.model,
            "status": self.status,
            "started_at": self.started_at,
            "tool_count": self.tool_count,
        }


class AgentSupervisor:
    """Thread-safe admission control and live-agent registry for one session.

    Agents run on worker threads (``asyncio.to_thread`` for sync tool calls,
    ``task_manager.start`` for background ones), so every method takes an
    ``RLock``. Following the same contract as ``AgentNameRegistry``: callers
    must not hold the lock across an ``await``, and no method here does — the
    one operation that touches foreign code, ``interrupt``, fires the abort
    *outside* the lock, matching ``kill_async_agent``'s ordering.
    """

    def __init__(
        self,
        *,
        max_concurrent: int | None = None,
        max_depth: int | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._live: dict[str, _LiveAgent] = {}
        self._paused = False
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None else max_concurrent_children()
        )
        self._max_depth = max_depth if max_depth is not None else max_spawn_depth()

    # -- capacity ---------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def max_depth(self) -> int:
        return self._max_depth

    def live_count(self) -> int:
        """How many agents are admitted and not yet released."""
        with self._lock:
            return len(self._live)

    def has_live_descendants(self, subagent_id: str) -> bool:
        """Keep ancestor resources alive even after an intermediate child exits."""
        with self._lock:
            return any(subagent_id in entry.ancestors for entry in self._live.values())

    # -- admission --------------------------------------------------------

    def admit(
        self,
        *,
        subagent_id: str,
        parent_id: str | None = None,
        depth: int = 0,
        goal: str = "",
        model: str | None = None,
        abort_controller: Any = None,
    ) -> None:
        """Reserve a slot for ``subagent_id`` or raise :class:`AgentAdmissionError`.

        Refuses, in order, when delegation is paused, when ``depth`` exceeds the
        nesting cap, and when the session is already at capacity. Re-admitting an
        id that is already live is a no-op rather than an error, so a retry that
        races its own registration cannot double-count a slot.
        """
        with self._lock:
            if subagent_id in self._live:
                return

            if self._paused:
                raise AgentAdmissionError(
                    "Delegation is paused for this session, so no new agent was "
                    "started. Do the work directly, or ask the user to resume "
                    "delegation.",
                    reason="paused",
                )

            if depth > self._max_depth:
                raise AgentAdmissionError(
                    f"Agent nesting depth {depth} exceeds the limit of "
                    f"{self._max_depth}. Do this work in the current agent "
                    f"instead of spawning another one.",
                    reason="depth",
                )

            if len(self._live) >= self._max_concurrent:
                raise AgentAdmissionError(
                    f"This session already has {len(self._live)} agents running, "
                    f"which is the limit of {self._max_concurrent}. Wait for one "
                    f"to finish before spawning another, or tell the user they "
                    f"can raise {_ENV_MAX_CONCURRENT}.",
                    reason="capacity",
                )

            parent = self._live.get(parent_id) if parent_id else None
            ancestors = (
                ((*parent.ancestors, parent_id) if parent else (parent_id,))
                if parent_id
                else ()
            )
            self._live[subagent_id] = _LiveAgent(
                subagent_id=subagent_id,
                parent_id=parent_id,
                depth=depth,
                goal=goal,
                model=model,
                abort_controller=abort_controller,
                ancestors=ancestors,
            )

    def release(self, subagent_id: str) -> bool:
        """Free the slot held by ``subagent_id``. Returns True iff one was held.

        Call this when the worker has actually exited, not when its status was
        merely labelled terminal. Codex holds its ``SpawnReservation`` for the
        same window; releasing on the label instead would let a replacement
        generation start while the old one is still running.
        """
        with self._lock:
            return self._live.pop(subagent_id, None) is not None

    # -- live state -------------------------------------------------------

    def set_status(self, subagent_id: str, status: str) -> None:
        """Update a live agent's status. No-op once it has been released."""
        with self._lock:
            agent = self._live.get(subagent_id)
            if agent is not None:
                agent.status = status

    def is_interrupted(self, subagent_id: str) -> bool:
        """Whether this agent was interrupted. False once it has been released.

        Read it BEFORE :meth:`release` — a finished run has to know how it
        ended in order to report that honestly, and the entry is gone
        afterwards.
        """
        with self._lock:
            agent = self._live.get(subagent_id)
            return agent is not None and agent.status == "interrupted"

    def set_tool_count(self, subagent_id: str, count: int) -> None:
        """Set a live agent's tool counter. No-op once it has been released.

        Absolute rather than incremental: the caller already holds a
        ``ProgressTracker`` with the authoritative running total, and a
        progress hook that fires more than once per tool would double-count an
        incremental API.
        """
        with self._lock:
            agent = self._live.get(subagent_id)
            if agent is not None:
                agent.tool_count = count

    # -- controls ---------------------------------------------------------

    def set_paused(self, paused: bool) -> bool:
        """Set whether new spawns are admitted. Returns the resulting state.

        Pausing does not touch agents already running — it only closes the door
        on new ones, which is what the overlay's pause control means.
        """
        with self._lock:
            self._paused = bool(paused)
            return self._paused

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def interrupt(self, subagent_id: str) -> bool:
        """Abort a live agent's run. Returns True iff the id was live.

        The entry is deliberately *not* removed: the slot stays held until the
        worker reaches its own exit and calls :meth:`release`. Status flips to
        ``interrupted`` so a snapshot taken during the wind-down window tells
        the truth — the agent is stopping, not stopped.
        """
        with self._lock:
            agent = self._live.get(subagent_id)
            if agent is None:
                return False
            agent.status = "interrupted"
            controller = agent.abort_controller

        # Outside the lock: ``.abort()`` runs foreign code. Same ordering as
        # ``kill_async_agent``, which aborts after leaving the registry lock.
        if controller is not None:
            try:
                controller.abort("interrupted by user")
            except Exception:  # noqa: BLE001 - an abort must never propagate
                pass
        return True

    # -- observation ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Authoritative state, in ``DelegationStatusResponse`` wire shape.

        Ordered by start time so the overlay renders a stable list rather than
        one that reshuffles on dict iteration order between polls.
        """
        with self._lock:
            active = [a.to_wire() for a in sorted(self._live.values(), key=lambda a: a.started_at)]
            return {
                "active": active,
                "max_concurrent_children": self._max_concurrent,
                "max_spawn_depth": self._max_depth,
                "paused": self._paused,
            }
