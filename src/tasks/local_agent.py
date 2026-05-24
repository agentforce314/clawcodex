"""``local_agent`` task type — full lifecycle (Chunk C / WI-2.3).

Builds on the Chunk-B skeleton with the chapter's full state shape and
lifecycle helpers. Two upstream pieces lock in here:

* ``output_file`` is wired to the sidechain JSONL transcript path
  (Chunk C / WI-2.2 — gate-zero) so Phase 3 / WI-3.1 (notification XML)
  and Phase 7 / WI-7.4 (auto-resume) have something to point at.
* ``progress`` carries a live ``AgentProgress`` snapshot fed by the
  ``ProgressTracker`` machinery (WI-2.4) — the chapter-correct token
  arithmetic plus the cap-5 recent-activities ring.

The lifecycle helpers (``register_async_agent``, ``queue_pending_message``,
``drain_pending_messages``, ``complete_agent_task``, ``fail_agent_task``,
``kill_async_agent``) are the named API that ``_launch_async_agent`` and
the future ``resume_agent_background`` route through. Each helper
treats ``runtime_tasks.update`` as the single atomic-mutation
primitive — the A6/C5 contract (mutator must be sync; never await
under the registry lock) is honored throughout.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TYPE_CHECKING

from src.tasks_core import TaskStateBase, is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry
    from src.tasks.progress import AgentProgress


@dataclass(kw_only=True)
class LocalAgentTaskState(TaskStateBase):
    """Runtime state for a background ``local_agent`` task.

    Field accounting (chapter-correct full set; refactoring plan WI-2.3
    now lands what WI-1.5 stubbed):

    Stays from base:
        id, type, status, description, start_time, output_file (now
        the sidechain JSONL path), output_offset, notified, tool_use_id,
        end_time, total_paused_seconds.

    Extension fields:
        agent_id            — same string as ``id``; kept as a separate
                              field for chapter-fidelity with TS.
        agent_type          — agent definition's ``agent_type``
                              (general-purpose, worker, fork, ...).
        prompt              — the user-supplied prompt text.
        selected_agent      — the resolved AgentDefinition; loose-typed
                              to avoid an import cycle.
        model               — optional per-spawn model override.
        abort_event         — asyncio.Event for cooperative kill.
        pending_messages    — Chunk D / WI-3.3 inbox; SendMessage
                              queues here for delivery at next tool
                              round.
        is_backgrounded     — was this spawned async or promoted later?
        retain              — UI is "holding" this task; blocks
                              eviction (Chunk D / WI-3.4).
        disk_loaded         — bootstrap has read the JSONL transcript
                              and merged it into ``messages``.
        evict_after         — Chunk D / WI-3.4 eviction grace deadline.
        progress            — live ``AgentProgress`` snapshot.
        last_reported_*     — delta-tracking for SDK progress events.
        result_text / error — final output container.
    """

    type: Literal["local_agent"] = "local_agent"  # type: ignore[assignment]
    agent_id: str = ""
    agent_type: str = ""
    prompt: str = ""
    selected_agent: Any = None  # AgentDefinition; loose to avoid cycles.
    model: str | None = None
    abort_event: asyncio.Event | None = field(default=None, repr=False, compare=False)
    pending_messages: list[str] = field(default_factory=list)
    is_backgrounded: bool = True
    retain: bool = False
    disk_loaded: bool = False
    evict_after: float | None = None
    progress: "AgentProgress | None" = None
    last_reported_tool_count: int = 0
    last_reported_token_count: int = 0
    result_text: str = ""
    error: str | None = None
    # Chunk F / WI-7.4 race guard. Set True by the auto-resume claim
    # mutator when this terminal state is being re-spawned; concurrent
    # SendMessage callers see the flag and back off to queueing the
    # message onto the resumed agent's pending_messages instead.
    # Reset to False on the fresh state ``register_async_agent`` upserts.
    is_resuming: bool = False
    # Phase M5: permission config set by Manager via TaskDirectives.
    # These fields allow Manager to control worker permission behavior.
    permission_mode: str | None = None
    always_allow_rules: list[dict[str, str]] | None = None


def is_local_agent_task(state: Any) -> bool:
    return isinstance(state, LocalAgentTaskState)


def is_local_agent_task_terminal(state: Any) -> bool:
    """Terminal-state predicate scoped to local_agent tasks."""
    return is_local_agent_task(state) and is_terminal_task_status(state.status)


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def register_async_agent(
    *,
    agent_id: str,
    description: str,
    prompt: str,
    agent_type: str,
    selected_agent: Any = None,
    model: str | None = None,
    tool_use_id: str | None = None,
    registry: "RuntimeTaskRegistry",
) -> LocalAgentTaskState:
    """Register a brand-new background agent on the runtime registry.

    Idempotent enough to be safe under spawn-then-resume races; if an
    entry already exists at ``agent_id`` it is replaced (the resume
    path explicitly wants this so the new event-loop wiring takes
    over).

    Returns the just-registered state so callers can hold a reference
    without re-fetching.

    The transcript path is computed (and the parent dir created) before
    registration so any consumer reading ``state.output_file`` can rely
    on the path being valid filesystem-side. ``output_file`` carries
    the JSONL transcript per WI-2.2 — Phase 3 / WI-3.1 cites it in the
    notification XML, Phase 7 / WI-7.4 reads it for auto-resume.
    """
    # Local import — transcript depends on a CSPRNG-validated agent_id,
    # registered task system imports back through here, defer to keep
    # the cycle untangled.
    from src.agent.transcript import get_agent_transcript_path

    output_file = get_agent_transcript_path(agent_id)

    state = LocalAgentTaskState(
        id=agent_id,
        type="local_agent",
        status="running",
        description=description,
        start_time=time.time(),
        output_file=output_file,
        agent_id=agent_id,
        agent_type=agent_type,
        prompt=prompt,
        selected_agent=selected_agent,
        model=model,
        tool_use_id=tool_use_id,
        is_backgrounded=True,
    )
    registry.upsert(state)
    return state


def queue_pending_message(
    task_id: str,
    message: str,
    registry: "RuntimeTaskRegistry",
    priority: str = "normal",
) -> bool:
    """Append a message to the agent's pending-messages inbox.

    Returns True iff the inbox was updated. Refuses to queue against a
    terminal-state task (the chapter's ``isTerminalTaskStatus`` guard
    in TS LocalAgentTask.tsx; mirrored via
    ``is_local_agent_task_terminal``).

    Priority handling (Phase M2):
    - ``critical`` / ``high``: prepend to head — worker processes next turn
    - ``normal``: append to tail — FIFO order

    Drained at tool-round boundaries by ``drain_pending_messages``
    (Chunk D / WI-3.3 hooks the drain into ``run_agent``).
    """
    queued = False

    def _enqueue(prev: TaskStateBase) -> TaskStateBase:
        nonlocal queued
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if is_terminal_task_status(prev.status):
            return prev
        queued = True
        if priority in ("critical", "high"):
            # Prepend to head — worker processes next turn
            prefix = "[CRITICAL] " if priority == "critical" else "[HIGH] "
            return replace(prev, pending_messages=[prefix + message, *prev.pending_messages])
        # normal: append to tail
        return replace(prev, pending_messages=[*prev.pending_messages, message])

    registry.update(task_id, _enqueue)
    return queued


def drain_pending_messages(
    task_id: str,
    registry: "RuntimeTaskRegistry",
) -> list[str]:
    """Atomically pop every pending message; return them in priority order.

    Sized for the per-turn drain in ``run_agent`` (Chunk D / WI-3.3) —
    the entire inbox is consumed at the boundary, the agent processes
    each message in priority order, then the next turn begins.

    Priority ordering (Phase M3):
    - critical messages ([CRITICAL] prefix) are returned first
    - high messages ([HIGH] prefix) are returned second
    - normal messages (no prefix) are returned last (FIFO within each band)
    """
    drained: list[str] = []

    def _empty(prev: TaskStateBase) -> TaskStateBase:
        nonlocal drained
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if not prev.pending_messages:
            return prev
        messages = list(prev.pending_messages)
        # Sort by priority: critical > high > normal
        def _priority_key(msg: str) -> tuple[int, int]:
            if msg.startswith("[CRITICAL] "):
                return (0, 0)
            if msg.startswith("[HIGH] "):
                return (1, 0)
            return (2, 0)
        messages.sort(key=_priority_key)
        drained = messages
        return replace(prev, pending_messages=[])

    registry.update(task_id, _empty)
    return drained


def update_agent_progress(
    task_id: str,
    progress: "AgentProgress",
    registry: "RuntimeTaskRegistry",
) -> None:
    """Replace the agent's progress snapshot. No-op on terminal state.

    Mirrors LocalAgentTask.tsx:339-353. Preserves ``progress.summary``
    if a background-summarization service has set one — the per-message
    progress update should not clobber the summary text.
    """
    def _set(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if prev.status != "running":
            return prev
        existing_summary = prev.progress.summary if prev.progress is not None else None
        new_progress = progress
        if existing_summary is not None:
            new_progress = replace(progress, summary=existing_summary)
        return replace(prev, progress=new_progress)

    registry.update(task_id, _set)


def _terminal_replace(
    prev: "LocalAgentTaskState",
    *,
    status: str,
    result_text: str | None = None,
    error: str | None = None,
    grace_seconds: float | None = None,
    now: float | None = None,
) -> "LocalAgentTaskState":
    """Compose the standard terminal-state mutation: status flip,
    end_time stamp, and ``evict_after`` deadline (Chunk D / WI-3.4).

    Pure helper — no registry mutation, no async work. The
    ``schedule_eviction`` helper checks ``retain`` and returns the
    state unchanged when the UI has pinned it.
    """
    # Local import — Chunk-D eviction module is allowed to depend on
    # local_agent (it imports the dataclass for the type guard) but
    # not the other way around at module scope.
    from src.tasks.eviction import PANEL_GRACE_SECONDS, schedule_eviction

    moment = now if now is not None else time.time()
    if grace_seconds is None:
        grace_seconds = PANEL_GRACE_SECONDS

    extras: dict[str, Any] = {"status": status, "end_time": moment}
    if result_text is not None:
        extras["result_text"] = result_text
    if error is not None:
        extras["error"] = error
    transitioned = replace(prev, **extras)
    return schedule_eviction(transitioned, grace_seconds=grace_seconds, now=moment)


def complete_agent_task(
    task_id: str,
    *,
    result_text: str,
    registry: "RuntimeTaskRegistry",
) -> None:
    """Flip status to ``completed``, stash the final text, schedule eviction."""
    def _complete(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if is_terminal_task_status(prev.status):
            return prev
        return _terminal_replace(prev, status="completed", result_text=result_text)

    registry.update(task_id, _complete)


def fail_agent_task(
    task_id: str,
    *,
    error: str,
    registry: "RuntimeTaskRegistry",
) -> None:
    """Flip status to ``failed``, record error, schedule eviction."""
    def _fail(prev: TaskStateBase) -> TaskStateBase:
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if is_terminal_task_status(prev.status):
            return prev
        # Mirror error into result_text so TaskOutput callers reading
        # the final text still see something useful (TS does this).
        return _terminal_replace(
            prev,
            status="failed",
            error=error,
            result_text=prev.result_text or error,
        )

    registry.update(task_id, _fail)


def kill_async_agent(
    task_id: str,
    registry: "RuntimeTaskRegistry",
    *,
    enqueue_notification: bool = True,
) -> None:
    """Signal abort on the agent's event, flip status to ``killed``,
    schedule eviction, optionally emit a ``<task-notification>``.

    Cooperative — the agent's run loop must check ``abort_event`` at
    yield points. Hard-cancel via ``asyncio.Task.cancel`` is a Phase-3+
    concern.

    ``enqueue_notification`` defaults to True (the chapter-correct
    behavior — the parent agent should learn the task was stopped via
    the same XML envelope as completion/failure). Callers that want to
    suppress the notification — e.g. the ``stop_task`` SDK path that
    emits its own SDK event — pass False.
    """
    aborted_event: asyncio.Event | None = None
    captured_description: str | None = None
    captured_output_file: str = ""
    fired = False

    def _kill(prev: TaskStateBase) -> TaskStateBase:
        nonlocal aborted_event, captured_description, captured_output_file, fired
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if is_terminal_task_status(prev.status):
            return prev
        aborted_event = prev.abort_event
        captured_description = prev.description
        captured_output_file = prev.output_file
        fired = True
        return _terminal_replace(prev, status="killed")

    registry.update(task_id, _kill)
    # Set the event OUTSIDE the registry lock so a misbehaving Event
    # subclass that schedules a callback can't deadlock against the
    # registry. (Default ``asyncio.Event`` is a thin flag; this is
    # defense-in-depth for the A6/C5 contract.)
    if aborted_event is not None:
        try:
            aborted_event.set()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "failed to set abort event for killed agent %s", task_id
            )

    # Notification emission OUTSIDE the lock as well — `enqueue_agent_notification`
    # internally takes the registry lock for its check-and-set.
    if fired and enqueue_notification and captured_description is not None:
        # Local import — task_notification depends on local_agent for
        # the type guard, so importing back here at module scope
        # would cycle. The deferred import is consistent with the
        # other lifecycle helpers.
        from src.utils.task_notification import enqueue_agent_notification

        enqueue_agent_notification(
            task_id=task_id,
            description=captured_description,
            status="killed",
            output_file=captured_output_file,
            registry=registry,
        )


# ---------------------------------------------------------------------------
# Task adapter — async kill via the lifecycle helper
# ---------------------------------------------------------------------------


class LocalAgentTask:
    """``Task`` adapter for ``local_agent`` entries.

    Polymorphic dispatch target for ``stop_task`` (Phase 5). The
    Chunk-C body delegates to ``kill_async_agent`` so the registry
    update + abort signal stay in one place.
    """

    name: str = "LocalAgentTask"
    type: Literal["local_agent"] = "local_agent"

    async def kill(
        self, task_id: str, registry: "RuntimeTaskRegistry"
    ) -> None:
        kill_async_agent(task_id, registry)


__all__ = [
    "LocalAgentTaskState",
    "LocalAgentTask",
    "is_local_agent_task",
    "is_local_agent_task_terminal",
    "register_async_agent",
    "queue_pending_message",
    "drain_pending_messages",
    "update_agent_progress",
    "complete_agent_task",
    "fail_agent_task",
    "kill_async_agent",
]
