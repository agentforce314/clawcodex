"""Owned worktrees: require isolation and preserve every changed checkout."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.utils.git import _run_git, create_worktree, get_repo_root, remove_worktree

logger = logging.getLogger(__name__)


@dataclass
class AgentWorktree:
    path: Path
    cwd: Path
    repository: Path
    branch: str
    initial_head: str
    retained: bool = False
    closed: bool = False
    in_use: Callable[[], bool] | None = None

    @classmethod
    def create(cls, base_cwd: str, name: str) -> "AgentWorktree":
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
            raise ValueError("Invalid agent worktree name")
        base = Path(base_cwd).resolve()
        root = get_repo_root(str(base))
        if root is None:
            raise RuntimeError("Worktree isolation requires an existing Git repository")
        repository = Path(root).resolve()
        path = repository.parent / name
        if not create_worktree(
            str(path), branch=name, cwd=str(repository), new_branch=True
        ):
            raise RuntimeError(f"Could not create isolated worktree at {path}")
        head, _, code = _run_git(["rev-parse", "HEAD"], str(path))
        if code:
            raise RuntimeError(
                f"Could not inspect created worktree at {path}; it was preserved"
            )
        return cls(path, path / base.relative_to(repository), repository, name, head)

    def close(self) -> None:
        """Remove only an unchanged checkout and the branch we created for it."""
        if self.closed:
            return
        self.closed = True
        self.retained = True
        if self.in_use is not None and self.in_use():
            return
        status, _, status_code = _run_git(
            ["status", "--porcelain", "--untracked-files=all"], str(self.path)
        )
        head, _, head_code = _run_git(["rev-parse", "HEAD"], str(self.path))
        if status_code or head_code or status or head != self.initial_head:
            return
        # Git itself checks for concurrent edits; never use --force here.
        if remove_worktree(str(self.path), cwd=str(self.repository)):
            self.retained = False
            _run_git(["branch", "-d", self.branch], str(self.repository))
        else:
            logger.warning(
                "Preserved agent worktree that could not be removed: %s", self.path
            )

    def notice(self) -> str:
        return f"Worktree changes preserved at {self.path} (branch {self.branch})."
