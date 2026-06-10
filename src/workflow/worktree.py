"""Per-agent git worktree isolation for ``agent(..., isolation="worktree")``.

Each isolated agent runs in a throwaway worktree named ``wf_<runId>-<idx>``,
created as a sibling of the main working tree and removed when the agent
finishes. If creation fails (not a git repo, etc.) the context manager yields
``None`` and the caller runs in place — isolation is best-effort, never fatal.

Cleanup is **on-exit only**: there is no crash-recovery sweep, so a process kill
(or a ``remove_worktree`` failure) can orphan a ``wf_*`` worktree. The git calls
run in a thread (``asyncio.to_thread``) so they don't block the workflow's event
loop / serialize parallel worktree setup.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from src.utils.git import create_worktree, remove_worktree

logger = logging.getLogger(__name__)


def worktree_slug(run_id: str, index: str) -> str:
    """The ``wf_<runId>-<idx>`` directory name.

    ``index`` is the deterministic call-path key (unique per agent); dots become
    dashes for a clean filesystem slug. Distinct keys can't collide after the
    transform because call-path digits are positionally unique."""
    safe_index = str(index).replace(".", "-")
    return f"{run_id}-{safe_index}"


@asynccontextmanager
async def agent_worktree(run_id: str, index: str, base_cwd: str) -> AsyncIterator[Optional[str]]:
    """Create a worktree for one agent and remove it on exit.

    Yields the worktree path, or ``None`` if it couldn't be created."""
    base = Path(base_cwd).resolve()
    wt_path = base.parent / worktree_slug(run_id, index)
    created = False
    try:
        created = await asyncio.to_thread(create_worktree, str(wt_path), cwd=str(base))
    except Exception:  # noqa: BLE001 — isolation is best-effort
        logger.debug("worktree create failed for %s", wt_path, exc_info=True)
        created = False
    try:
        yield str(wt_path) if created else None
    finally:
        if created:
            try:
                await asyncio.to_thread(remove_worktree, str(wt_path), cwd=str(base), force=True)
            except Exception:  # noqa: BLE001
                logger.debug("worktree remove failed for %s", wt_path, exc_info=True)
