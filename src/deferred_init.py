"""Deferred post-render prefetches — chapter 2 §"Post-render deferred
prefetches" (ch02 round-3 GAP B).

Mirrors the minimal core of TS ``startDeferredPrefetches``
(main.tsx:392-439) and the non-interactive early kicks
(main.tsx:1973-1990): warm the memoized user context (CLAWCODEX.md walk)
and — when the session is trusted — the system context (git status
probes) so the first turn's ``fetch_system_prompt_parts`` hits warm
caches instead of paying the cold filesystem/subprocess cost inside the
user's first request.

Trust gating mirrors TS ``prefetchSystemContextIfSafe``: system context
(git subprocess execution in the workspace) only runs when the workspace
is trusted. ch02 round-4: the gate is ``check_trust_accepted(cwd)`` —
the session-flag short-circuit covers the parent's explicit/implicit
grants, and the persisted per-project verdict covers the agent-server
child, which never sets the in-process flag.

Two execution modes, because the port's entrypoints split by loop
ownership:

* **Running asyncio loop**: tasks are scheduled on it.
* **No loop** (headless, which calls ``asyncio.run`` per
  query): a daemon thread drives its own short-lived loop. The memo
  caches in ``context_system.prompt_assembly`` are plain module-level
  dict assignments — atomic under the GIL; a race with the first query
  recomputes the same value at worst (same tolerance TS accepts for its
  fire-and-forget ``void`` kicks).

Failures never propagate: prefetching is purely advisory.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DeferredPrefetchHandle:
    """Handle for tests/observability. ``join()`` blocks until the
    thread-mode warmup finishes; ``tasks`` carries loop-mode tasks."""

    mode: str  # "loop" | "thread" | "noop"
    tasks: list[asyncio.Task] = field(default_factory=list)
    thread: threading.Thread | None = None

    def join(self, timeout: float | None = 5.0) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)


def _system_context_allowed(cwd: str | None = None) -> bool:
    # ch02 round-4 WI-1: cwd-composed. The agent-server CHILD never sets
    # the in-process session flag (the parent's establish_session_trust
    # ran in the parent), so the old bootstrap-flag read kept the git half
    # dead on the interactive path. check_trust_accepted embeds the
    # session-flag short-circuit (parent semantics unchanged) and
    # otherwise reads the persisted per-project verdict for THIS cwd.
    try:
        from src.services.startup_gates import check_trust_accepted

        return check_trust_accepted(cwd)
    except Exception:
        return False


async def _warm(cwd: str | None, include_system_context: bool) -> None:
    # The whole body is guarded: in loop mode a failure outside the
    # gather (e.g. the import) would otherwise surface as an unretrieved
    # task exception — prefetching is advisory and must never propagate.
    try:
        from src.context_system.prompt_assembly import (
            get_system_context,
            get_user_context,
        )

        coros = [get_user_context(cwd)]
        if include_system_context:
            coros.append(get_system_context(cwd))
        results = await asyncio.gather(*coros, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.debug("deferred prefetch lane failed: %r", result)
    except Exception as exc:
        logger.debug("deferred prefetch failed: %r", exc)


def start_deferred_prefetches(
    cwd: str | None = None,
    *,
    include_system_context: bool | None = None,
) -> DeferredPrefetchHandle:
    """Fire-and-forget warmup of the per-session context memos.

    ``include_system_context=None`` resolves the trust gate itself
    (TS ``prefetchSystemContextIfSafe``); pass ``True`` from a
    just-accepted trust gate (TS ``interactiveHelpers.tsx:159`` kicks
    ``getSystemContext`` right after trust is established).

    Idempotent: the underlying context functions are memoized, so
    re-kicking (e.g. once at mount, again after the trust gate) only
    fills lanes that are still cold.
    """
    if include_system_context is None:
        include_system_context = _system_context_allowed(cwd)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(_warm(cwd, include_system_context))
        return DeferredPrefetchHandle(mode="loop", tasks=[task])

    def _run_in_thread() -> None:
        try:
            asyncio.run(_warm(cwd, include_system_context))
        except Exception as exc:
            logger.debug("deferred prefetch thread failed: %r", exc)

    thread = threading.Thread(
        target=_run_in_thread,
        name="clawcodex-deferred-prefetch",
        daemon=True,
    )
    thread.start()
    return DeferredPrefetchHandle(mode="thread", thread=thread)
