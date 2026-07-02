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
#
# ch03 round-4 GAP C (inbound ch02 commitment): keyed by resolved cwd.
# The old single-snapshot cache returned whatever was cached regardless of
# the cwd argument, so on a multi-session --http server session B's prompt
# could carry session A's git status. TS gets per-process correctness for
# free (one cwd per process); the port's multi-session server needs the
# key. Dict item writes are GIL-atomic; a race recomputes the same value.
# ---------------------------------------------------------------------------

_git_context_cache: dict[str, GitContextSnapshot] = {}
_git_is_repo_cache: dict[str, bool] = {}


def _cache_key(cwd: str | None) -> str:
    target = cwd or os.getcwd()
    try:
        return os.path.realpath(target)
    except OSError:
        return str(target)


def clear_git_caches() -> None:
    """Clear the memoized git context caches — all cwds (call after compact)."""
    _git_context_cache.clear()
    _git_is_repo_cache.clear()


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
    """Check if the CWD is inside a git repository. Memoized per cwd."""
    key = _cache_key(cwd)
    cached = _git_is_repo_cache.get(key)
    if cached is not None:
        return cached

    target = cwd or os.getcwd()
    result = _git_cmd(["rev-parse", "--is-inside-work-tree"], target)
    verdict = result == "true"
    _git_is_repo_cache[key] = verdict
    return verdict


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
    Results are memoized per cwd; call clear_git_caches() to invalidate.
    """
    key = _cache_key(cwd)
    cached = _git_context_cache.get(key)
    if cached is not None:
        return cached

    target = cwd or os.getcwd()

    if not get_is_git(target):
        snapshot = GitContextSnapshot(available=False, error="Not a git repository")
        _git_context_cache[key] = snapshot
        return snapshot

    loop = asyncio.get_event_loop()

    branch_fut = loop.run_in_executor(
        None, _git_cmd, ["rev-parse", "--abbrev-ref", "HEAD"], target,
    )
    default_branch_fut = loop.run_in_executor(
        None, _get_default_branch, target,
    )
    status_fut = loop.run_in_executor(
        # --no-optional-locks: don't take index write locks for the
        # status probe — a concurrent `git` in the user's other terminal
        # must never block on ours (TS context.ts:63-72; the flag rides
        # only status + log there — TS-exact, ch03 round-3 G2).
        None, _git_cmd, ["--no-optional-locks", "status", "--short"], target,
    )
    commits_fut = loop.run_in_executor(
        None, _git_cmd, ["--no-optional-locks", "log", "--oneline", "-n", "5"], target,
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
        # TS-exact truncation tail (context.ts:88) — tells the model how
        # to get the full status instead of leaving a dead end.
        status = status[:MAX_STATUS_CHARS] + (
            '\n... (truncated because it exceeds 2k characters. '
            'If you need more information, run "git status" using BashTool)'
        )
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
    _git_context_cache[key] = snapshot
    return snapshot


# ---------------------------------------------------------------------------
# format_git_status — format for system context injection
# ---------------------------------------------------------------------------

def format_git_status(ctx: GitContextSnapshot) -> str:
    """
    Format git context as a string for the systemContext.gitStatus key.

    ch03 round-4 GAP C: TS-exact model-facing text (context.ts:96-103).
    The "snapshot in time" preamble is load-bearing prompt engineering —
    it tells the model the status will NOT update during the conversation,
    which stops it from treating the block as live output to refresh; and
    "Main branch (you will usually use this for PRs)" carries an
    instruction the neutral "Default branch" label lost. Blocks join with
    blank lines, status falls back to "(clean)" — byte-shape parity with
    the TS join('\\n\\n') list.
    """
    if not ctx.available:
        return ""

    parts: list[str] = [
        "This is the git status at the start of the conversation. Note that "
        "this status is a snapshot in time, and will not update during the "
        "conversation.",
    ]

    if ctx.branch:
        parts.append(f"Current branch: {ctx.branch}")
    if ctx.default_branch:
        parts.append(
            f"Main branch (you will usually use this for PRs): {ctx.default_branch}"
        )
    if ctx.user_name:
        parts.append(f"Git user: {ctx.user_name}")

    # The truncation notice (when any) is already embedded in ctx.status
    # with the TS-exact wording by collect_git_context.
    parts.append(f"Status:\n{ctx.status or '(clean)'}")

    if ctx.recent_commits:
        parts.append(f"Recent commits:\n{ctx.recent_commits}")

    return "\n\n".join(parts)
