"""``in_process_teammate`` task type — Chunk F / WI-6.2 + sub-WI-6.2.a.

Mirrors ``typescript/src/tasks/InProcessTeammateTask/types.ts:22-77``.
Implements the chapter's full state shape for in-process teammates
(swarm peers running in the same Python process under
``AsyncLocalStorage``-equivalent isolation) plus the two-level abort
hook semantics.

Per-gap-analysis-§5 reframe: this module is "rename-and-rewrite, not
extend" relative to the existing ``services/swarm/teammate.py``. Only
the status enum (post ``CANCELLED → KILLED`` rename) and the
``TeammateConfig`` field shape carry forward; everything else is
green-field. The legacy ``Teammate``/``TeammateManager`` are NOT
deleted from this module — that's a separate teardown ticket — but
all chapter-10 callers should reach for the typed state in this
module instead.

**Sub-WI-6.2.a — two-level abort hook semantics:**

Two ``asyncio.Event`` fields on the state, with two distinct
exception classes raised when each fires:

* ``current_work_abort_event`` set → ``CurrentWorkAbortedError`` →
  outer lifecycle CATCHES, clears the event, loops to next pending
  message (the "redirect" pattern).
* ``abort_event`` set → ``TeammateAbortedError`` → outer lifecycle
  does NOT catch; the teammate exits ("kill" pattern).

Both subclass ``asyncio.CancelledError`` so existing cancellation
paths (e.g. ``asyncio.Task.cancel()`` semantics) keep working without
special-casing.

**Note — Python 3.11+ ExceptionGroup interaction (flag F2):** the
catch clauses use specific subclass names (``except CurrentWorkAbortedError:``)
which is correct for the current flat-await shape. If the run loop
is later wrapped in ``asyncio.TaskGroup``, the catches need
``except* CurrentWorkAbortedError`` (PEP 654 syntax). Today's run
loop is a flat await; flagged here so a future TaskGroup refactor
remembers to update.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING, TypeVar

from src.tasks_core import TaskStateBase, is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry

# ---------------------------------------------------------------------------
# Two-level abort exception hierarchy (sub-WI-6.2.a)
# ---------------------------------------------------------------------------


class TeammateAbortedError(asyncio.CancelledError):
    """Raised when ``abort_event`` fires.

    The outer teammate lifecycle does NOT catch this — it propagates
    up and unwinds the lifecycle. The teammate exits.

    Subclassing ``asyncio.CancelledError`` means standard cancellation
    paths (``asyncio.Task.cancel()`` semantics, ``await asyncio.shield``)
    treat it correctly without special handling.
    """


class CurrentWorkAbortedError(asyncio.CancelledError):
    """Raised when ``current_work_abort_event`` fires.

    The outer teammate lifecycle CATCHES this, clears the event, and
    loops to pick up the next pending message — the "redirect" pattern.
    The teammate continues running.
    """


# ---------------------------------------------------------------------------
# TeammateIdentity — chapter-10 identity record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeammateIdentity:
    """Stable identity for an in-process teammate.

    Mirrors TS ``InProcessTeammateTask/types.ts:13-20``. Values are
    plain data (no live handles) so the identity can be serialized to
    AppState and survive process restarts. Permission-mode and
    plan-mode-required policy live here because they're set at spawn
    time and don't change over the teammate's lifetime.
    """

    agent_id: str  # e.g. "researcher@my-team"
    agent_name: str  # e.g. "researcher"
    team_name: str
    parent_session_id: str = ""
    color: str | None = None
    plan_mode_required: bool = False


# ---------------------------------------------------------------------------
# Message-cap helper (chapter §"In-Process Teammates" — whale-session OOM guard)
# ---------------------------------------------------------------------------

# Per chapter §"In-Process Teammates": cap the UI-facing
# ``messages`` snapshot at 50 entries. The actual conversation
# continues with full history on disk; this cap only bounds the
# AppState mirror that the zoomed-transcript view reads. BQ analysis
# (round 9, 2026-03-20) showed ~20MB RSS per agent at 500+ turns;
# whale session 9a990de8 reached 36.8GB across 292 agents in 2 minutes.
TEAMMATE_MESSAGES_UI_CAP: int = 50

T = TypeVar("T")


def append_capped_message(prev: list[T] | None, item: T) -> list[T]:
    """Append ``item`` to ``prev``, capping the result at
    ``TEAMMATE_MESSAGES_UI_CAP`` entries by dropping the oldest.

    Always returns a new list — callers passing a frozen / shared
    list don't have to worry about mutation.
    """
    if prev is None or len(prev) == 0:
        return [item]
    if len(prev) >= TEAMMATE_MESSAGES_UI_CAP:
        return list(prev[-(TEAMMATE_MESSAGES_UI_CAP - 1):]) + [item]
    return list(prev) + [item]


# ---------------------------------------------------------------------------
# InProcessTeammateTaskState — chapter's full field set
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class InProcessTeammateTaskState(TaskStateBase):
    """Runtime state for a single in-process swarm teammate.

    Mirrors ``typescript/src/tasks/InProcessTeammateTask/types.ts:22-77``.
    Field accounting matches the chapter:

    * ``identity`` — the stable ``TeammateIdentity`` record.
    * ``prompt`` / ``model`` / ``selected_agent`` — spawn inputs.
    * ``abort_event`` / ``current_work_abort_event`` — two-level abort
      (sub-WI-6.2.a). See exception classes above.
    * ``awaiting_plan_approval`` — flag flipped True when the
      teammate submits a plan via ExitPlanMode; the leader's
      ``plan_approval_response`` clears it.
    * ``permission_mode`` — per-teammate permission mode (cycled
      independently of the leader).
    * ``messages`` — UI-facing conversation snapshot, capped at
      ``TEAMMATE_MESSAGES_UI_CAP`` via ``append_capped_message``.
    * ``in_progress_tool_use_ids`` — tool_use_ids currently being
      executed (used for animation in transcript view).
    * ``pending_user_messages`` — inbox for user-typed messages
      delivered when viewing the teammate's transcript.
    * ``is_idle`` — work-stealing flag; an idle teammate consumes no
      tokens and is waiting for the next message.
    * ``shutdown_requested`` — cooperative-termination flag set by
      the leader's ``shutdown_request``; the teammate winds down at
      a natural stopping point.
    * ``on_idle_callbacks`` — runtime-only hooks for orchestration
      patterns (e.g. "wait for all teammates to idle").
    * ``progress`` / ``last_reported_*`` — same shape as
      LocalAgentTaskState's progress fields.
    """

    type: Literal["in_process_teammate"] = "in_process_teammate"  # type: ignore[assignment]
    identity: TeammateIdentity = field(
        default_factory=lambda: TeammateIdentity(
            agent_id="", agent_name="", team_name="",
        )
    )
    prompt: str = ""
    model: str | None = None
    selected_agent: Any = None  # AgentDefinition; loose to avoid cycles
    abort_event: asyncio.Event | None = field(default=None, repr=False, compare=False)
    current_work_abort_event: asyncio.Event | None = field(
        default=None, repr=False, compare=False
    )
    awaiting_plan_approval: bool = False
    # TODO (Phase 9): tighten to ``permissions.types.PermissionMode``
    # Literal once the permission-forwarding bridge lands. Loose-typed
    # for now so the chapter-10 task layer doesn't pull in the
    # permissions module's typing — flagged per critic Phase-6 N2.
    permission_mode: str = "default"
    error: str | None = None
    result: Any = None
    progress: Any = None  # AgentProgress; loose to avoid cycles
    messages: list[Any] = field(default_factory=list)
    in_progress_tool_use_ids: set[str] = field(default_factory=set)
    pending_user_messages: list[str] = field(default_factory=list)
    is_idle: bool = False
    shutdown_requested: bool = False
    on_idle_callbacks: list[Callable[[], None]] = field(
        default_factory=list, repr=False, compare=False
    )
    last_reported_tool_count: int = 0
    last_reported_token_count: int = 0


def is_in_process_teammate_task(state: Any) -> bool:
    return isinstance(state, InProcessTeammateTaskState)


# ---------------------------------------------------------------------------
# Two-level abort hook (sub-WI-6.2.a)
# ---------------------------------------------------------------------------


def check_abort_events(state: InProcessTeammateTaskState) -> None:
    """Raise the appropriate exception if either abort event is set.

    Called by the teammate's run loop between yield points. The
    chapter-correct ordering: ``abort_event`` (kill, stronger intent)
    is checked FIRST so a "both events fired in the same yield"
    scenario surfaces ``TeammateAbortedError`` rather than
    ``CurrentWorkAbortedError``. The kill wins.

    No-op when neither event is set (or when the event fields are
    None — defensive for state objects constructed without them).
    """
    abort = state.abort_event
    if abort is not None and abort.is_set():
        raise TeammateAbortedError(
            f"teammate {state.identity.agent_id!r} kill event fired"
        )
    current_work = state.current_work_abort_event
    if current_work is not None and current_work.is_set():
        raise CurrentWorkAbortedError(
            f"teammate {state.identity.agent_id!r} current-work redirect"
        )


async def run_with_two_level_abort(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    state_provider: Callable[[], InProcessTeammateTaskState | None],
) -> T:
    """Wrap a coroutine with the two-level abort policy.

    Polls the registry-provided state via ``state_provider`` between
    awaits — the ``current_work_abort_event`` and ``abort_event``
    references can change as the teammate is reset / re-spawned, so
    we resolve them fresh on each tick rather than capturing them.

    Returns the wrapped coroutine's result on normal completion.
    Raises:
    * ``TeammateAbortedError`` if ``abort_event`` fires (caller MUST
      NOT catch — this is the kill signal).
    * ``CurrentWorkAbortedError`` if ``current_work_abort_event``
      fires (caller's outer loop is expected to catch and continue).

    This is a thin helper for unit-testable two-level-abort behavior.
    Production integration (the actual run_agent loop wiring) lands
    when the in-process teammate spawn path goes live in Phase 7.
    """
    task = asyncio.ensure_future(coro_factory())
    try:
        while not task.done():
            state = state_provider()
            if state is not None:
                # ``check_abort_events`` raises before we wait again —
                # callers see the right exception class on the next
                # ``await`` boundary.
                try:
                    check_abort_events(state)
                except (TeammateAbortedError, CurrentWorkAbortedError):
                    task.cancel()
                    # Drain the cancellation; ignore the resulting
                    # CancelledError — we'll re-raise our typed
                    # variant below.
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise
            # Tick interval — small enough to feel responsive,
            # large enough not to burn CPU.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
            except asyncio.TimeoutError:
                continue  # task not done yet; re-check abort events
        return task.result()
    finally:
        if not task.done():
            task.cancel()


def outer_lifecycle_should_catch(exc: BaseException) -> bool:
    """Helper for the outer-loop catch policy.

    Returns True for ``CurrentWorkAbortedError`` (the teammate should
    redirect to the next pending message) and False for
    ``TeammateAbortedError`` (the teammate should exit). Plain
    ``asyncio.CancelledError`` returns False — the kill path; the
    teammate should respect the cancel.

    Lets the outer loop in the eventual run_agent integration look
    like:

    ```python
    while not state.shutdown_requested:
        try:
            await _run_one_turn(...)
        except (TeammateAbortedError, CurrentWorkAbortedError) as exc:
            if outer_lifecycle_should_catch(exc):
                # Clear the event and loop again.
                state.current_work_abort_event.clear()
                continue
            raise
    ```
    """
    return isinstance(exc, CurrentWorkAbortedError)


# ---------------------------------------------------------------------------
# Task adapter — kill via the two-level signal
# ---------------------------------------------------------------------------


class InProcessTeammateTask:
    """``Task`` adapter for ``in_process_teammate`` entries.

    Polymorphic dispatch target for ``stop_task`` (Phase 5). Setting
    ``abort_event`` propagates the kill signal; the teammate's run
    loop sees ``TeammateAbortedError`` at its next yield and
    unwinds the lifecycle.
    """

    name: str = "InProcessTeammateTask"
    type: Literal["in_process_teammate"] = "in_process_teammate"

    async def kill(
        self, task_id: str, registry: "RuntimeTaskRegistry"
    ) -> None:
        aborted_event: asyncio.Event | None = None

        def _kill(prev: TaskStateBase) -> TaskStateBase:
            nonlocal aborted_event
            if not isinstance(prev, InProcessTeammateTaskState):
                return prev
            if is_terminal_task_status(prev.status):
                return prev
            aborted_event = prev.abort_event
            return replace(prev, status="killed")

        registry.update(task_id, _kill)
        # Set the event OUTSIDE the registry lock — same defense-in-depth
        # pattern as kill_async_agent. asyncio.Event is thread-safe for
        # ``set()`` (it dispatches via the loop's call_soon_threadsafe
        # internally), but we don't want a misbehaving subclass to
        # deadlock the registry.
        if aborted_event is not None:
            try:
                aborted_event.set()
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "failed to set abort event for killed teammate %s", task_id
                )


__all__ = [
    "TeammateAbortedError",
    "CurrentWorkAbortedError",
    "TeammateIdentity",
    "TEAMMATE_MESSAGES_UI_CAP",
    "append_capped_message",
    "InProcessTeammateTaskState",
    "InProcessTeammateTask",
    "is_in_process_teammate_task",
    "check_abort_events",
    "run_with_two_level_abort",
    "outer_lifecycle_should_catch",
]
