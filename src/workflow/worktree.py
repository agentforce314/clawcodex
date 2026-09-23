"""Strict workflow isolation with preservation of changed worktrees."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from src.agent.worktree import AgentWorktree

def worktree_slug(run_id: str, index: str) -> str:
    return f"{run_id}-{str(index).replace('.', '-')}"


@asynccontextmanager
async def agent_worktree(run_id: str, index: str, base_cwd: str) -> AsyncIterator[str]:
    """Yield a real isolated working directory, or fail before running work."""
    worktree = await asyncio.to_thread(
        AgentWorktree.create, base_cwd, worktree_slug(run_id, index)
    )
    try:
        yield str(worktree.cwd)
    finally:
        await asyncio.to_thread(worktree.close)
