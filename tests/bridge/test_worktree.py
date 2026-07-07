"""Tests for ``src.bridge.worktree`` — real-git happy/fallback paths.

These tests use real ``git`` subprocesses in pytest tmp dirs. They
require ``git`` on PATH; if it's missing they degrade to fallback
assertions (which is the right semantic — the helper itself falls back
when git is missing, so the test asserts that contract).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from src.bridge.worktree import (
    WorktreePaths,
    create_agent_worktree,
    remove_agent_worktree,
)


def _git(args: list[str], cwd: str) -> None:
    """Synchronous git helper used to set up fixture repos."""
    subprocess.run(
        ['git', *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture()
def git_repo(tmp_path):
    """Create an initialized repo with one commit. Returns the path."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(['init', '--initial-branch=main'], cwd=str(repo))
    _git(['config', 'user.email', 'test@example.com'], cwd=str(repo))
    _git(['config', 'user.name', 'Test'], cwd=str(repo))
    _git(['config', 'commit.gpgsign', 'false'], cwd=str(repo))
    (repo / 'README.md').write_text('hello\n')
    _git(['add', 'README.md'], cwd=str(repo))
    _git(['commit', '-m', 'init'], cwd=str(repo))
    return str(repo)


# ── happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_then_remove_happy_path(git_repo: str) -> None:
    """A fresh worktree is created at the conventional path and removed
    cleanly. ``created=True`` and the working_dir actually exists and
    is its own checkout."""
    paths = await create_agent_worktree(git_repo, 'sess-abc')
    try:
        assert paths.created is True
        assert paths.base_dir == git_repo
        expected = os.path.join(
            git_repo, '.clawcodex', 'worktrees', 'agent-sess-abc',
        )
        assert paths.working_dir == expected
        # The worktree exists on disk with the README from HEAD.
        assert os.path.isdir(expected)
        assert os.path.exists(os.path.join(expected, 'README.md'))
        # ``git worktree list`` confirms the new worktree.
        out = subprocess.check_output(
            ['git', 'worktree', 'list'], cwd=git_repo,
        ).decode()
        assert expected in out
    finally:
        await remove_agent_worktree(paths)

    # After removal: directory is gone and ``git worktree list`` no
    # longer mentions it.
    assert not os.path.isdir(paths.working_dir)
    out = subprocess.check_output(
        ['git', 'worktree', 'list'], cwd=git_repo,
    ).decode()
    assert 'agent-sess-abc' not in out


@pytest.mark.asyncio
async def test_remove_is_idempotent_when_not_created(tmp_path) -> None:
    """``created=False`` paths are a no-op on remove — never raises,
    never invokes git."""
    paths = WorktreePaths(
        base_dir=str(tmp_path), working_dir=str(tmp_path), created=False,
    )
    # Should not raise even though base_dir isn't a git repo.
    await remove_agent_worktree(paths)


# ── session_id validation (path traversal guard) ───────────────────────


@pytest.mark.parametrize('bad_id', [
    '../escape',
    '..',
    'foo/bar',
    'foo\\bar',
    '',
    'a' * 65,  # too long
    'has space',
    'with;semi',
    'with.dot',  # dot is not in our allowlist
])
@pytest.mark.asyncio
async def test_session_id_validation_falls_back(
    git_repo: str, bad_id: str,
) -> None:
    """Any session_id that fails the allowlist must fall back to the
    base dir without invoking git. No worktree dir is created."""
    paths = await create_agent_worktree(git_repo, bad_id)
    assert paths.created is False
    assert paths.working_dir == git_repo
    # No .clawcodex/worktrees/agent-<bad> is created.
    expected = os.path.join(
        git_repo, '.clawcodex', 'worktrees', f'agent-{bad_id}',
    )
    assert not os.path.exists(expected)


@pytest.mark.parametrize('good_id', [
    'cse_abc123',
    'session-with-dashes',
    'session_with_underscores',
    'a',
    'A1' * 32,  # exactly 64 chars
])
@pytest.mark.asyncio
async def test_session_id_validation_accepts_allowlist(
    git_repo: str, good_id: str,
) -> None:
    """Allowlist-conformant session_ids create real worktrees."""
    paths = await create_agent_worktree(git_repo, good_id)
    try:
        assert paths.created is True
        assert paths.working_dir.endswith(f'agent-{good_id}')
    finally:
        await remove_agent_worktree(paths)


# ── fallback paths ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_when_not_a_git_repo(tmp_path) -> None:
    """A non-repo base dir falls back to itself with ``created=False``;
    nothing on disk changes."""
    base = str(tmp_path)
    paths = await create_agent_worktree(base, 'sess-x')
    assert paths.created is False
    assert paths.working_dir == base
    assert paths.base_dir == base
    # No .clawcodex/worktrees created on fallback.
    assert not os.path.exists(os.path.join(base, '.clawcodex', 'worktrees'))


@pytest.mark.asyncio
async def test_fallback_when_worktree_add_fails(
    git_repo: str, monkeypatch,
) -> None:
    """If ``git worktree add`` returns non-zero (e.g. path already
    occupied as a worktree), we fall back instead of raising."""
    # Pre-create the target as a real worktree, so a second ``add`` at
    # the same path is guaranteed to fail.
    target = os.path.join(
        git_repo, '.clawcodex', 'worktrees', 'agent-sess-dupe',
    )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    subprocess.run(
        ['git', 'worktree', 'add', '--detach', target, 'HEAD'],
        cwd=git_repo, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        paths = await create_agent_worktree(git_repo, 'sess-dupe')
        # ``git worktree add`` should refuse to overwrite, falling back.
        assert paths.created is False
        assert paths.working_dir == git_repo
    finally:
        # Cleanup the seeded worktree directly.
        subprocess.run(
            ['git', 'worktree', 'remove', '--force', target],
            cwd=git_repo, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@pytest.mark.asyncio
async def test_create_with_uncommitted_edits_in_worktree(
    git_repo: str,
) -> None:
    """A worktree with dirty edits is still removable via ``--force``."""
    paths = await create_agent_worktree(git_repo, 'sess-dirty')
    assert paths.created is True
    # Simulate the session writing files inside the worktree.
    dirty = os.path.join(paths.working_dir, 'session-output.txt')
    with open(dirty, 'w', encoding='utf-8') as fh:
        fh.write('mid-session output\n')
    # Remove should still succeed because we pass --force.
    await remove_agent_worktree(paths)
    assert not os.path.isdir(paths.working_dir)


# ── error & timeout handling ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_git_returns_negative_rc_when_git_binary_missing(
    monkeypatch, tmp_path,
) -> None:
    """If the git binary can't be spawned (FileNotFoundError /
    PermissionError / etc.), ``create_agent_worktree`` must fall back
    rather than propagate the OSError."""
    from src.bridge import worktree as wt_mod

    async def fail_subprocess(*_a, **_kw):
        raise PermissionError('mocked permission denied')

    monkeypatch.setattr(
        asyncio, 'create_subprocess_exec', fail_subprocess,
    )
    paths = await wt_mod.create_agent_worktree(
        str(tmp_path), 'cse_perm_fail',
    )
    assert paths.created is False
    assert paths.working_dir == str(tmp_path)


@pytest.mark.asyncio
async def test_run_git_times_out_and_falls_back(
    monkeypatch, tmp_path,
) -> None:
    """A wedged git subprocess (communicate never returns) must be
    killed by the timeout path; ``create_agent_worktree`` falls back."""
    from src.bridge import worktree as wt_mod

    # Shorten the timeout for the test.
    monkeypatch.setattr(wt_mod, '_GIT_TIMEOUT_S', 0.05)

    class WedgedProc:
        returncode = None

        async def communicate(self):
            # Block forever — simulating a hung ``git`` process.
            await asyncio.sleep(60)
            return b'', b''

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_subprocess(*_a, **_kw):
        return WedgedProc()

    monkeypatch.setattr(
        asyncio, 'create_subprocess_exec', fake_subprocess,
    )
    # _is_git_repo's call to _run_git will time out first; we fall
    # back because that returns non-zero.
    paths = await wt_mod.create_agent_worktree(
        str(tmp_path), 'cse_wedged',
    )
    assert paths.created is False
    assert paths.working_dir == str(tmp_path)


@pytest.mark.asyncio
async def test_run_git_kills_subprocess_on_cancellation(
    monkeypatch, tmp_path,
) -> None:
    """If the outer task is cancelled mid-``communicate()``, the
    subprocess must be killed in the ``finally`` block — not left
    running past the cancellation."""
    from src.bridge import worktree as wt_mod

    kill_called: list[bool] = []
    wait_called: list[bool] = []

    class CancellableProc:
        returncode: int | None = None

        async def communicate(self):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # Simulate the kernel's behavior: when the parent
                # task is cancelled, communicate() raises before
                # the subprocess exits on its own.
                raise
            return b'', b''

        def kill(self):
            kill_called.append(True)
            self.returncode = -9

        async def wait(self):
            wait_called.append(True)
            return self.returncode or -9

    proc = CancellableProc()

    async def fake_subprocess(*_a, **_kw):
        return proc

    monkeypatch.setattr(
        asyncio, 'create_subprocess_exec', fake_subprocess,
    )

    task = asyncio.create_task(
        wt_mod._run_git('rev-parse', '--is-inside-work-tree', cwd=str(tmp_path)),
    )
    # Yield once so the task starts and reaches `await communicate()`.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The finally block must have killed and drained the subprocess.
    assert kill_called == [True], 'kill() should fire on cancel'
    assert wait_called == [True], 'wait() should drain after kill'


# ── concurrency ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_sessions_get_distinct_worktrees(
    git_repo: str,
) -> None:
    """Two sessions in the same base dir get isolated worktrees and
    each has its own HEAD checkout."""
    paths_a = await create_agent_worktree(git_repo, 'sess-a')
    paths_b = await create_agent_worktree(git_repo, 'sess-b')
    try:
        assert paths_a.created and paths_b.created
        assert paths_a.working_dir != paths_b.working_dir
        # Editing in one doesn't affect the other.
        with open(
            os.path.join(paths_a.working_dir, 'a.txt'),
            'w', encoding='utf-8',
        ) as fh:
            fh.write('a\n')
        assert not os.path.exists(
            os.path.join(paths_b.working_dir, 'a.txt'),
        )
    finally:
        await remove_agent_worktree(paths_a)
        await remove_agent_worktree(paths_b)
