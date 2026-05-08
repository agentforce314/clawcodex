"""Foreground → background agent promotion — Chunk H / WI-10.1.

Mirrors the ``Promise.race`` pattern in
``typescript/src/tasks/LocalAgentTask/LocalAgentTask.tsx:519-651``.
The TS sync agent loop races "next message from agent" against
"background signal"; on the bg-signal branch it cleanly returns the
foreground iterator (triggering its ``finally`` for resource cleanup)
and re-spawns the agent as async.

Python equivalent: ``asyncio.wait({next_msg_task, bg_signal_task},
return_when=FIRST_COMPLETED)`` — see the `Promise.race` direct
translation. ``gather`` would wait for both, which is wrong.

The chapter calls out four abort scenarios that have to behave
correctly:

* **Foreground ESC** — kills both the agent and its parent (shared
  abort controller).
* **Background ESC after promotion** — kills only the background
  agent; the parent (now wholly separate) is unaffected.
* **Background signal during foreground** — promotion: foreground
  iterator returns cleanly, background spawn with same agent_id,
  abort controllers swap, ``is_backgrounded`` flips True.
* **Background signal during background** — no-op; the agent is
  already backgrounded.

Atomicity contract: no messages lost in the transition; no zombie
iterators left running. The ``asyncio.wait`` pattern enforces this
because the unfinished task is explicitly cancelled in the bg-signal
branch.

Per A6/C5: the promotion mutator (which flips ``is_backgrounded`` on
``LocalAgentTaskState``) is sync — runs under ``runtime_tasks.update``
without ``await``. The actual asyncio races happen *outside* the
registry lock.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, AsyncIterator, TYPE_CHECKING

from src.tasks.local_agent import LocalAgentTaskState

if TYPE_CHECKING:
    from src.task_registry import RuntimeTaskRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifecycle helpers — register agent as foreground / promote to background
# ---------------------------------------------------------------------------


def register_agent_foreground(
    *,
    agent_id: str,
    registry: "RuntimeTaskRegistry",
) -> None:
    """Mark the agent as foreground (``is_backgrounded=False``).

    Called when an agent is first spawned via the synchronous Agent
    tool path (no ``run_in_background: true``). The state must
    already exist in ``runtime_tasks`` (via ``register_async_agent``);
    this helper just flips the flag.
    """
    def _flip(prev: Any) -> Any:
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if prev.is_backgrounded is False:
            return prev  # already foreground
        return replace(prev, is_backgrounded=False)

    registry.update(agent_id, _flip)


def register_agent_background(
    *,
    agent_id: str,
    registry: "RuntimeTaskRegistry",
) -> bool:
    """Promote a running foreground agent to background.

    Mutates atomically under the registry lock; returns True iff the
    promotion happened (False means the state was missing, terminal,
    or already backgrounded — all idempotent no-ops).
    """
    promoted = False

    def _flip(prev: Any) -> Any:
        nonlocal promoted
        if not isinstance(prev, LocalAgentTaskState):
            return prev
        if prev.status != "running":
            return prev  # terminal — no point promoting
        if prev.is_backgrounded is True:
            return prev  # already backgrounded — idempotent
        promoted = True
        return replace(prev, is_backgrounded=True)

    registry.update(agent_id, _flip)
    return promoted


def unregister_agent_foreground(
    *,
    agent_id: str,
    registry: "RuntimeTaskRegistry",
) -> None:
    """Drop the agent's runtime entry — called when a foreground
    agent completes WITHOUT being promoted to background. The
    registry no longer needs to track it (the parent has the result
    inline)."""
    registry.remove(agent_id)


# ---------------------------------------------------------------------------
# The race itself — foreground generator vs. background signal
# ---------------------------------------------------------------------------


async def run_with_background_escape(
    agent_iterator: AsyncIterator[Any],
    *,
    background_signal: asyncio.Event,
    on_background: Any = None,
) -> tuple[list[Any], bool]:
    """Drive the foreground agent iterator, racing each ``next``
    against the background signal.

    Returns ``(messages, was_backgrounded)``:
    * ``messages`` — every message the iterator yielded before the
      race ended.
    * ``was_backgrounded`` — True iff ``background_signal`` fired
      mid-iteration (caller should re-spawn as async); False iff the
      iterator completed naturally.

    On bg-signal: the foreground iterator's pending ``next`` task is
    cancelled (cleanly — ``async for`` machinery uses ``__anext__``
    which respects cancellation). The optional ``on_background``
    callback fires inside the bg-signal branch so the caller can
    swap abort controllers / flip ``is_backgrounded`` while the
    state is still well-defined.
    """
    messages: list[Any] = []

    while True:
        next_task = asyncio.ensure_future(_anext(agent_iterator))
        bg_task = asyncio.ensure_future(background_signal.wait())

        done, pending = await asyncio.wait(
            {next_task, bg_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if bg_task in done and next_task not in done:
            # Background signal fired first — cancel the pending
            # next-message future, drain its cancellation, fire the
            # callback. The agent's __anext__ machinery handles the
            # cancellation cleanly via its finally clauses.
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, StopAsyncIteration, Exception):
                # Swallow on cancellation drain — the agent is being
                # backgrounded, so any in-flight ``__anext__`` and
                # its finalizer exceptions don't matter; the
                # message that would have been yielded is discarded
                # by design (the new background spawn re-iterates
                # from the resumed state). Catching ``Exception``
                # rather than just ``CancelledError`` covers
                # finalizer-raised errors from ``finally`` clauses
                # that may surface during ``cancel()``.
                pass
            if on_background is not None:
                try:
                    result = on_background()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("on_background callback raised")
            return messages, True

        # next_task completed (and possibly bg_task too — but the
        # next message came in first or alongside; deliver it). We
        # cancel bg_task because we're about to start a fresh wait
        # with a new bg_task in the next iteration.
        if not bg_task.done():
            bg_task.cancel()
            try:
                await bg_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            msg = next_task.result()
        except StopAsyncIteration:
            return messages, False
        except Exception:
            # Iterator raised — re-raise so the caller's exception
            # handlers see it.
            raise
        messages.append(msg)


async def _anext(iterator: AsyncIterator[Any]) -> Any:
    """Helper — calls ``__anext__`` directly so the ensure_future
    target is one well-defined coroutine."""
    return await iterator.__anext__()


__all__ = [
    "register_agent_foreground",
    "register_agent_background",
    "unregister_agent_foreground",
    "run_with_background_escape",
]
