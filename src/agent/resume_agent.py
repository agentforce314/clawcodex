"""Auto-resume for terminal local_agent tasks — Chunk F / WI-7.4.

Mirrors ``typescript/src/tools/AgentTool/resumeAgent.ts``. When
SendMessage targets a terminal-state agent (completed / failed /
killed), instead of returning an error this module re-spawns the
agent with the prior conversation reconstructed from its sidechain
JSONL transcript (Chunk C / WI-2.2 — gate-zero).

DIP claim (concern C6 from refactoring-plan review): this module
depends on ``TranscriptReader`` (the interface, not the writer's IO
layer) — the reader is the canonical consumer for ``state.output_file``.

Race guard
----------

Two concurrent SendMessage calls to the same dead agent_id should
NOT both spawn replacement runs. The atomic claim:

1. Read the registry entry.
2. If state is terminal AND ``not state.is_resuming``, set
   ``is_resuming=True`` and return "this caller wins."
3. Else return "another caller is resuming; queue the message via
   ``queue_pending_message`` instead."

The check + flip are one ``runtime_tasks.update`` mutator call —
atomic under the registry's RLock. Two concurrent callers see
exactly one winner.

Pending-message handoff
-----------------------

The caller that wins the resume race carries the SendMessage payload
into the resumed run by passing it as the new ``prompt``. Losers
queue their messages onto the new running state via
``queue_pending_message`` (which by then sees the running entry the
winner just registered).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, TYPE_CHECKING

from src.agent.transcript import TranscriptReader
from src.tasks.local_agent import (
    LocalAgentTaskState,
    register_async_agent,
)
from src.tasks_core import is_terminal_task_status

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumeResult:
    """Outcome of a resume attempt.

    * ``resumed``: True iff this caller won the race and re-spawned
      the agent. False means another caller got there first or the
      target agent isn't actually terminal.
    * ``agent_id``: the resumed agent's id (same as the original).
    * ``output_file``: transcript path on disk.
    * ``replayed_message_count``: number of messages reconstructed
      from the transcript.
    * ``reason``: human-readable status (only populated on the loser
      / no-op paths).
    """

    resumed: bool
    agent_id: str
    output_file: str = ""
    replayed_message_count: int = 0
    reason: str = ""


def _claim_resume(
    agent_id: str,
    runtime_tasks: "RuntimeTaskRegistry",
) -> tuple[bool, LocalAgentTaskState | None]:
    """Atomic check-and-claim — race-safe resume gate.

    Returns ``(won, prev_state)``:
    * ``(True, terminal_state)`` — caller is the resume winner; the
      registry entry now has ``is_resuming=True`` so concurrent
      callers see the terminal flag and back off.
    * ``(False, None)`` — caller lost (or task isn't resumable).

    The ``is_resuming`` bookkeeping lives on
    ``LocalAgentTaskState.is_resuming`` (Chunk-F field; the dataclass
    grew the flag for this WI).

    **Load-bearing invariant (critic Chunk-F C1):** the path from this
    helper to ``register_async_agent`` (in
    ``resume_agent_background``) MUST stay synchronous. Loser callers
    rely on observing the winner's *running* fresh state — they fall
    back to ``queue_pending_message``, which refuses terminal states.
    If a future refactor makes ``_reconstruct_messages_from_transcript``
    async (e.g., streaming reads of large transcripts), the winner
    yields control mid-path; the loser then observes
    ``is_resuming=True`` on a still-terminal state and the
    ``queue_pending_message`` no-ops, silently dropping the loser's
    message. If async is needed later, the fix is to gate the
    loser's ``queue_pending_message`` with a state-refresh-loop that
    waits for the winner to land the running state.
    """
    won = False
    captured: LocalAgentTaskState | None = None

    def _maybe_claim(prev: Any) -> Any:
        nonlocal won, captured
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if not is_terminal_task_status(prev.status):
            return prev  # not terminal — nothing to resume
        if getattr(prev, "is_resuming", False):
            return prev  # someone else is already resuming
        won = True
        captured = prev
        return replace(prev, is_resuming=True)

    runtime_tasks.update(agent_id, _maybe_claim)
    return won, captured


def _reconstruct_messages_from_transcript(transcript_path: str) -> list[Any]:
    """Read the JSONL transcript and return parseable message objects.

    Tolerant of trailing partial lines (writer-crashed-mid-write —
    the same case the chapter-correct ``TranscriptReader`` already
    handles). Returns the raw dict / Message objects that the reader
    yields; the caller decides how to hydrate them into typed
    ``Message`` subclasses.
    """
    return TranscriptReader(transcript_path).read_all()


async def resume_agent_background(
    *,
    agent_id: str,
    prompt: str,
    context: "ToolContext",
) -> ResumeResult:
    """Re-spawn a stopped agent's background lifecycle with ``prompt``
    as the resume message.

    Returns a ``ResumeResult`` describing the outcome:

    * Winner of the race → ``resumed=True``; the registry holds a
      fresh ``LocalAgentTaskState`` for ``agent_id`` with status
      ``"running"``. The transcript is read from disk and counted in
      ``replayed_message_count`` for the caller's diagnostics.
    * Loser → ``resumed=False``, ``reason`` describes the situation.
      Caller should typically follow up with ``queue_pending_message``
      to deliver the prompt to the now-running agent.
    * Target not terminal / not present → ``resumed=False`` with a
      reason like ``"task not terminal"`` or ``"task not found"``.

    **The resume run does NOT actually drive a model call in this
    chunk.** Wiring the resumed lifecycle into ``run_agent`` requires
    threading the reconstructed messages and the resume prompt through
    ``RunAgentParams`` — that's a subsequent integration step. This
    chunk lands the structural primitive (race-safe re-registration +
    transcript replay scaffolding) so SendMessage's auto-resume
    branch has something to call. The resumed entry's ``status`` is
    ``"running"`` so other callers see it and queue rather than
    re-resume.
    """
    runtime = context.runtime_tasks
    state = runtime.get(agent_id)

    if state is None:
        return ResumeResult(
            resumed=False, agent_id=agent_id,
            reason="task not found in runtime_tasks",
        )

    if not isinstance(state, LocalAgentTaskState):
        return ResumeResult(
            resumed=False, agent_id=agent_id,
            reason=f"task type {state.type!r} is not local_agent",
        )

    if not is_terminal_task_status(state.status):
        return ResumeResult(
            resumed=False, agent_id=agent_id,
            reason=f"task is {state.status!r}, not terminal",
        )

    won, prev = _claim_resume(agent_id, runtime)
    if not won or prev is None:
        # Another caller won the race; the registry entry is now
        # ``is_resuming=True`` (and likely already replaced by the
        # winner with a fresh running state). Return a no-op so the
        # SendMessage caller knows to fall back to queueing.
        return ResumeResult(
            resumed=False, agent_id=agent_id,
            reason="another caller is resuming; queue your message instead",
        )

    # Reconstruct the prior conversation. Errors here are non-fatal —
    # the resumed run still gets the new prompt; it just lacks the
    # historical context.
    transcript_path = prev.output_file
    replayed: list[Any] = []
    try:
        replayed = _reconstruct_messages_from_transcript(transcript_path)
    except Exception:
        logger.exception(
            "transcript reconstruction failed for %s; resuming without history",
            agent_id,
        )

    # Re-register the agent with a fresh running state. ``register_async_agent``
    # ``upsert``s, replacing the terminal entry. The new state has
    # ``is_resuming=False`` (default) so a future resume can fire if
    # this run also completes. Carry the resume ``prompt`` into
    # pending_messages so the resumed run picks it up at its first
    # tool-round drain (chapter-correct behavior — Chunk D / WI-3.3).
    fresh_state = register_async_agent(
        agent_id=agent_id,
        description=prev.description,
        prompt=prompt,  # the SendMessage payload is the resume prompt
        agent_type=prev.agent_type,
        selected_agent=prev.selected_agent,
        model=prev.model,
        tool_use_id=prev.tool_use_id,
        registry=runtime,
    )

    return ResumeResult(
        resumed=True,
        agent_id=agent_id,
        output_file=fresh_state.output_file,
        replayed_message_count=len(replayed),
        reason="",
    )


__all__ = [
    "ResumeResult",
    "resume_agent_background",
]
