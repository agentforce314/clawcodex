"""Bounded-concurrency scheduling for ``agent()`` fan-out.

A thin wrapper over ``asyncio.Semaphore`` (the canonical limiter pattern in
``src/services/tool_execution/orchestrator.py``). Each ``agent()`` call acquires
a :meth:`Scheduler.slot` for the duration of its subagent run, so no more than
``max_concurrent`` subagents run at once even when ``parallel()`` hands the
scheduler hundreds of coroutines. The per-run lifetime cap (1,000 agents) is
also enforced here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .constants import MAX_AGENTS_PER_RUN, max_concurrent_agents
from .errors import WorkflowLimitError


class Scheduler:
    def __init__(self, max_concurrent: int | None = None) -> None:
        self._max_concurrent = max_concurrent if max_concurrent is not None else max_concurrent_agents()
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._launched = 0
        self._peak = 0
        self._active = 0

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def launched(self) -> int:
        return self._launched

    @property
    def peak_concurrency(self) -> int:
        return self._peak

    def reserve(self) -> int:
        """Claim a lifetime slot and return this call's 0-based index.

        Raises once the per-run agent cap is hit. Called synchronously at
        ``agent()`` entry (before any ``await``) so indices are deterministic.
        """
        if self._launched >= MAX_AGENTS_PER_RUN:
            raise WorkflowLimitError(
                f"workflow exceeded the per-run agent cap of {MAX_AGENTS_PER_RUN}"
            )
        index = self._launched
        self._launched += 1
        return index

    @asynccontextmanager
    async def slot(self):
        """Hold a concurrency slot for the duration of one subagent run."""
        async with self._sem:
            self._active += 1
            self._peak = max(self._peak, self._active)
            try:
                yield
            finally:
                self._active -= 1
