"""Resume a background worker through its original, session-owned launcher."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from src.agent.transcript import TranscriptReader
from src.tasks_core import is_terminal_task_status
from src.types.messages import Message, message_from_dict

if TYPE_CHECKING:
    from src.tool_system.context import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class AgentContinuation:
    """Retain execution settings after a terminal task is evicted from the HUD.

    The lock serializes relaunches across threads. The completion event is set
    only after the previous worker has closed its transcript and released its
    admission slot, so a stopped worker cannot race its replacement's cleanup.
    """

    restart: Callable[[str, list[Message]], Any]
    output_file: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    finished: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class ResumeResult:
    """A successful result means an actual background lifecycle was started."""

    resumed: bool
    agent_id: str
    output_file: str = ""
    replayed_message_count: int = 0
    reason: str = ""


def _reconstruct_messages_from_transcript(transcript_path: str) -> list[Message]:
    return [
        message_from_dict(entry)
        for entry in TranscriptReader(transcript_path).read_all()
        if isinstance(entry, dict)
        and entry.get("role") in {"user", "assistant", "system"}
    ]


def _resume(*, agent_id: str, prompt: str, context: ToolContext) -> ResumeResult:
    continuation = context.agent_continuations.get(agent_id)
    state = context.runtime_tasks.get(agent_id)
    if state is not None and state.type != "local_agent":
        return ResumeResult(
            False, agent_id, reason=f"task type {state.type!r} is not local_agent"
        )
    if state is not None and not is_terminal_task_status(state.status):
        return ResumeResult(
            False, agent_id, reason=f"task is {state.status!r}, not terminal"
        )
    if continuation is None:
        return ResumeResult(
            False,
            agent_id,
            reason=(
                "task not found in this session"
                if state is None
                else "no executable continuation is available for this task"
            ),
        )
    with continuation.lock:
        state = context.runtime_tasks.get(agent_id)
        if state is not None and not is_terminal_task_status(state.status):
            return ResumeResult(
                False, agent_id, reason="another caller resumed this worker"
            )
        # A kill marks status immediately; do not overlap the still-exiting run.
        if not continuation.finished.wait(timeout=10):
            return ResumeResult(
                False,
                agent_id,
                reason="previous worker is still stopping; retry shortly",
            )
        try:
            replayed = _reconstruct_messages_from_transcript(continuation.output_file)
            continuation.restart(prompt, replayed)
        except Exception as exc:
            logger.exception("could not resume worker %s", agent_id)
            return ResumeResult(False, agent_id, reason=str(exc))
        return ResumeResult(True, agent_id, continuation.output_file, len(replayed))


async def resume_agent_background(
    *,
    agent_id: str,
    prompt: str,
    context: ToolContext,
) -> ResumeResult:
    """Replay history and launch the same worker ID with a new user message.

    Failed admission or a missing launcher leaves the terminal state intact.
    Work runs on the session TaskManager, independently of this tool's temporary
    event loop. Concurrent callers can queue to the winner's running worker.
    """
    return await asyncio.to_thread(
        _resume, agent_id=agent_id, prompt=prompt, context=context
    )


__all__ = ["AgentContinuation", "ResumeResult", "resume_agent_background"]
