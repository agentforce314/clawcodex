"""Per-agent git worktree isolation for ``agent(..., isolation="worktree")``.

Each isolated agent runs in a throwaway worktree named ``wf_<runId>-<idx>`` (the
slug the stale-worktree sweep recognizes), created as a sibling of the main
working tree and removed when the agent finishes. If creation fails (not a git
repo, etc.) the context manager yields ``None`` and the caller runs in place —
isolation is best-effort, never fatal.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from src.utils.git import create_worktree, remove_worktree

logger = logging.getLogger(__name__)


def worktree_slug(run_id: str, index: str) -> str:
    """The ``wf_<runId>-<idx>`` directory name (index dots → dashes for a clean
    filesystem slug)."""
    safe_index = str(index).replace(".", "-")
    return f"{run_id}-{safe_index}"


@contextmanager
def agent_worktree(run_id: str, index: str, base_cwd: str) -> Iterator[Optional[str]]:
    """Create a worktree for one agent and remove it on exit.

    Yields the worktree path, or ``None`` if it couldn't be created."""
    base = Path(base_cwd).resolve()
    wt_path = base.parent / worktree_slug(run_id, index)
    created = False
    try:
        created = create_worktree(str(wt_path), cwd=str(base))
    except Exception:  # noqa: BLE001 — isolation is best-effort
        logger.debug("worktree create failed for %s", wt_path, exc_info=True)
        created = False
    try:
        yield str(wt_path) if created else None
    finally:
        if created:
            try:
                remove_worktree(str(wt_path), cwd=str(base), force=True)
            except Exception:  # noqa: BLE001
                logger.debug("worktree remove failed for %s", wt_path, exc_info=True)
