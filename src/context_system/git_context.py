"""
Git context collection — aligned with typescript/src/context.ts getGitStatus().

Runs parallel git commands: branch, default branch, status --short,
log --oneline -n 5, config user.name.  Status truncated at 2000 chars.
Results are memoized per session; call clear_git_caches() to invalidate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import MAX_STATUS_CHARS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return type — richer than the old GitContext
# ---------------------------------------------------------------------------

@dataclass
class GitContextSnapshot:
    """Structured git context for prompt injection."""

    available: bool
    repo_root: str | None = None
    branch: str | None = None
    default_branch: str | None = None
    user_name: str | None = None
    status: str | None = None
    status_truncated: bool = False
    recent_commits: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Module-level cache (mirrors TS memoize on getGitStatus)
# ---------------------------------------------------------------------------

_git_context_cache: GitContextSnapshot | None = None
_git_is_repo_cache: bool | None = None


def clear_git_caches() -> None:
    """Clear the memoized git context cache (call after compact)."""
    global _git_context_cache, _git_is_repo_cache
    _git_context_cache = None
    _git_is_repo_cache = None


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------

def _git_cmd(
    args: list[str],
    cwd: str,
    timeout: float = 5.0,
) -> str:
    """Run a git command and return stripped stdout or '' on error."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _get_default_branch(cwd: str) -> str:
    """
    Detect the default branch (main/master/etc).

    Tries:
      1. refs/remotes/origin/HEAD symref
      2. Known default branch names
      3. Falls back to 'main'
    """
    head_ref = _git_cmd(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
    if head_ref:
        parts = head_ref.rsplit("/", 1)
        if len(parts) == 2:
            return parts[1]

    for candidate in ("main", "master", "develop"):
        check = _git_cmd(["rev-parse", "--verify", f"refs/heads/{candidate}"], cwd)
        if check:
            return candidate

    return "main"


# ---------------------------------------------------------------------------
# is_git_repo — quick check
# ---------------------------------------------------------------------------

def get_is_git(cwd: str | None = None) -> bool:
    """Check if the CWD is inside a git repository. Memoized."""
    global _git_is_repo_cache
    if _git_is_repo_cache is not None:
        return _git_is_repo_cache

    target = cwd or os.getcwd()
    result = _git_cmd(["rev-parse", "--is-inside-work-tree"], target)
    _git_is_repo_cache = result == "true"
    return _git_is_repo_cache


# ---------------------------------------------------------------------------
# collect_git_context — main entry point (mirrors TS getGitStatus)
# ---------------------------------------------------------------------------

async def collect_git_context(
    cwd: str | None = None,
) -> GitContextSnapshot:
    """
    Collect a comprehensive git context snapshot.

    Mirrors TS getGitStatus() from context.ts.
    Runs multiple git commands in parallel for speed.
    Results are memoized; call clear_git_caches() to invalidate.
    """
    global _git_context_cache
    if _git_context_cache is not None:
        return _git_context_cache

    target = cwd or os.getcwd()

    if not get_is_git(target):
        snapshot = GitContextSnapshot(available=False, error="Not a git repository")
        _git_context_cache = snapshot
        return snapshot

    loop = asyncio.get_event_loop()

    branch_fut = loop.run_in_executor(
        None, _git_cmd, ["rev-parse", "--abbrev-ref", "HEAD"], target,
    )
    default_branch_fut = loop.run_in_executor(
        None, _get_default_branch, target,
    )
    status_fut = loop.run_in_executor(
        None, _git_cmd, ["status", "--short"], target,
    )
    commits_fut = loop.run_in_executor(
        None, _git_cmd, ["log", "--oneline", "-n", "5"], target,
    )
    user_fut = loop.run_in_executor(
        None, _git_cmd, ["config", "user.name"], target,
    )
    root_fut = loop.run_in_executor(
        None, _git_cmd, ["rev-parse", "--show-toplevel"], target,
    )

    branch, default_branch, status, commits, user_name, repo_root = await asyncio.gather(
        branch_fut, default_branch_fut, status_fut, commits_fut, user_fut, root_fut,
    )

    status_truncated = False
    if status and len(status) > MAX_STATUS_CHARS:
        status = status[:MAX_STATUS_CHARS] + "\n... (truncated)"
        status_truncated = True

    snapshot = GitContextSnapshot(
        available=True,
        repo_root=repo_root or None,
        branch=branch or None,
        default_branch=default_branch or None,
        user_name=user_name or None,
        status=status or None,
        status_truncated=status_truncated,
        recent_commits=commits or None,
    )
    _git_context_cache = snapshot
    return snapshot


# ---------------------------------------------------------------------------
# format_git_status — format for system context injection
# ---------------------------------------------------------------------------

def format_git_status(ctx: GitContextSnapshot) -> str:
    """
    Format git context as a string for the systemContext.gitStatus key.

    Mirrors TS getGitStatus output format from context.ts.
    """
    if not ctx.available:
        return ""

    parts: list[str] = []
    parts.append("Git repository detected.")

    if ctx.branch:
        parts.append(f"Current branch: {ctx.branch}")
    if ctx.default_branch:
        parts.append(f"Default branch: {ctx.default_branch}")
    if ctx.user_name:
        parts.append(f"User: {ctx.user_name}")

    if ctx.status:
        parts.append(f"\nStatus:\n{ctx.status}")
        if ctx.status_truncated:
            parts.append("(Status output was truncated)")
    else:
        parts.append("\nWorking tree clean.")

    if ctx.recent_commits:
        parts.append(f"\nRecent commits:\n{ctx.recent_commits}")

    return "\n".join(parts)
