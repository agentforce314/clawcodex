"""Agent-server ``worktree_status`` / ``worktree_exit`` controls (--worktree).

The env block (``CLAWCODEX_WORKTREE_*``) is the channel the launcher uses to
advertise the session; the controls are gated on ``single_session`` (stdio
transport) so a multi-session --http server can never expose one client's
worktree to another.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.server.agent_server import AgentServerConfig, _AgentSession
from src.utils.worktree_session import create_worktree_for_session


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _restore_cwd():
    """worktree_exit's remove path os.chdir()s (so the dying backend doesn't
    hold its cwd inside the removed dir) — in tests the process lives on and
    the target is a pytest tmp dir that gets cleaned up, so restore."""
    before = os.getcwd()
    try:
        yield
    finally:
        os.chdir(before)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hi\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _make_session(single_session: bool = True) -> tuple[_AgentSession, list[dict]]:
    emitted: list[dict] = []
    sess = _AgentSession(
        session_id="wt-sess", cwd="/tmp",
        config=AgentServerConfig(single_session=single_session),
        loop=MagicMock(), out_queue=MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)  # type: ignore[method-assign]
    return sess, emitted


def _control(sess: _AgentSession, subtype: str, **params) -> None:
    asyncio.run(sess._handle_control_request({
        "type": "control_request",
        "request_id": "req-1",
        "request": {"subtype": subtype, **params},
    }))


def _last_reply(emitted: list[dict]) -> dict:
    for env in reversed(emitted):
        if env.get("type") == "control_response":
            return env["response"]["response"]
    raise AssertionError(f"no control_response in {emitted!r}")


def _env_for(repo: Path, name: str) -> dict[str, str]:
    session = create_worktree_for_session(name, cwd=str(repo))
    return session.to_env()


def test_status_reports_inactive_without_env():
    sess, emitted = _make_session()
    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith("CLAWCODEX_WORKTREE_")}
    with patch.dict(os.environ, clean_env, clear=True):
        _control(sess, "worktree_status")
    reply = _last_reply(emitted)
    assert reply == {"ok": True, "active": False}


def test_status_reports_counts_for_active_worktree(repo):
    env = _env_for(repo, "status-wt")
    wt_path = env["CLAWCODEX_WORKTREE_PATH"]
    (Path(wt_path) / "dirty.txt").write_text("x")

    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_status")
    reply = _last_reply(emitted)
    assert reply["ok"] is True
    assert reply["active"] is True
    assert reply["name"] == "status-wt"
    assert reply["branch"] == "worktree-status-wt"
    assert reply["path"] == wt_path
    assert reply["git_ok"] is True
    assert reply["dirty_files"] == 1
    assert reply["commits"] == 0


def test_controls_are_gated_off_multi_session_transports(repo):
    # Process-wide env on --http would let ANY WS client remove a worktree it
    # doesn't own; the gate reports inactive / refuses.
    env = _env_for(repo, "http-wt")
    sess, emitted = _make_session(single_session=False)
    with patch.dict(os.environ, env):
        _control(sess, "worktree_status")
        status = _last_reply(emitted)
        _control(sess, "worktree_exit", action="remove")
        exit_reply = _last_reply(emitted)
    assert status == {"ok": True, "active": False}
    assert exit_reply["ok"] is False
    assert os.path.isdir(env["CLAWCODEX_WORKTREE_PATH"])  # untouched


def test_exit_keep_replies_message_and_marks_done(repo):
    env = _env_for(repo, "keeper")
    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="keep")
        keep = _last_reply(emitted)
        _control(sess, "worktree_exit", action="keep")
        second = _last_reply(emitted)
        _control(sess, "worktree_status")
        status = _last_reply(emitted)
    assert keep["ok"] is True
    assert "Worktree kept" in keep["message"]
    assert env["CLAWCODEX_WORKTREE_PATH"] in keep["message"]
    assert os.path.isdir(env["CLAWCODEX_WORKTREE_PATH"])
    # done-latch: the flow is one-shot per session
    assert second["ok"] is False
    assert status == {"ok": True, "active": False}


def test_exit_remove_deletes_worktree_and_reports_no_changes(repo):
    env = _env_for(repo, "removee")
    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="remove")
    reply = _last_reply(emitted)
    assert reply["ok"] is True
    assert reply["message"] == "Worktree removed (no changes)"
    assert not os.path.exists(env["CLAWCODEX_WORKTREE_PATH"])
    assert _git(repo, "branch", "--list", "worktree-removee") == ""


def test_exit_remove_reports_discarded_work(repo):
    env = _env_for(repo, "discard")
    wt = Path(env["CLAWCODEX_WORKTREE_PATH"])
    (wt / "new.txt").write_text("x")
    _git(wt, "add", "new.txt")
    _git(wt, "commit", "-q", "-m", "wip")
    (wt / "extra.txt").write_text("y")

    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="remove")
    reply = _last_reply(emitted)
    assert reply["ok"] is True
    assert reply["message"] == (
        "Worktree removed. 1 commit and uncommitted changes were discarded."
    )
    assert not os.path.exists(str(wt))


def test_exit_remove_failure_reports_error_and_stays_active(repo):
    env = _env_for(repo, "stubborn")
    env["CLAWCODEX_WORKTREE_PATH"] = "/nonexistent/nowhere"
    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="remove")
        failed = _last_reply(emitted)
        _control(sess, "worktree_status")
        status = _last_reply(emitted)
    assert failed["ok"] is False
    assert failed["error"]
    # NOT latched done — the client may retry or fall back to keep.
    assert status["active"] is True


def test_exit_invalid_action_is_rejected(repo):
    env = _env_for(repo, "badaction")
    sess, emitted = _make_session()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="explode")
    reply = _last_reply(emitted)
    assert reply["ok"] is False
    assert "explode" in reply["error"]
    assert os.path.isdir(env["CLAWCODEX_WORKTREE_PATH"])


def test_exit_remove_is_refused_during_an_active_turn(repo):
    """Idle-only, like every destructive control (critic MAJOR): the turn
    runs on the worker thread while controls run on the main loop — removal
    mid-turn would delete the directory out from under live tool calls."""
    from src.utils.abort_controller import AbortController

    env = _env_for(repo, "busy-wt")
    sess, emitted = _make_session()
    sess._current_abort = AbortController()
    with patch.dict(os.environ, env):
        _control(sess, "worktree_exit", action="remove")
        refused = _last_reply(emitted)
        # keep stays allowed mid-turn — it deletes nothing.
        _control(sess, "worktree_exit", action="keep")
        kept = _last_reply(emitted)
    assert refused["ok"] is False
    assert "active turn" in refused["error"]
    assert os.path.isdir(env["CLAWCODEX_WORKTREE_PATH"])  # untouched
    assert kept["ok"] is True


def test_exit_remove_refuses_forged_branch_or_foreign_path(repo):
    """Defense-in-depth (critic): a split env block must not turn the
    best-effort `branch -D` into deleting an arbitrary branch, nor point
    removal outside .claude/worktrees/."""
    env = _env_for(repo, "forged")
    sess, emitted = _make_session()
    with patch.dict(os.environ, {**env, "CLAWCODEX_WORKTREE_BRANCH": "main"}):
        _control(sess, "worktree_exit", action="remove")
    forged_branch = _last_reply(emitted)
    assert forged_branch["ok"] is False
    assert "worktree-" in forged_branch["error"]
    assert _git(repo, "branch", "--list", "main").strip() != ""  # main survives

    outside = repo.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    with patch.dict(os.environ, {**env, "CLAWCODEX_WORKTREE_PATH": str(outside)}):
        _control(sess, "worktree_exit", action="remove")
    foreign_path = _last_reply(emitted)
    assert foreign_path["ok"] is False
    assert "outside" in foreign_path["error"]
    assert outside.is_dir()  # untouched
