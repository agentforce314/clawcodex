"""--worktree CLI wiring: parser shapes, nested-env hygiene at cli entry,
launcher env plumbing, and the headless keep-note path."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

from src.cli import _build_parser, _maybe_create_worktree, _WORKTREE_FAILED
from src.entrypoints.tui_launcher import _child_env
from src.utils.worktree_session import ENV_OWNER_PID, ENV_PATH, WorktreeSession


# ── parser ───────────────────────────────────────────────────────────────────

def test_parser_bare_flag_means_generate_name():
    args = _build_parser().parse_args(["-w", "-p", "hi"])
    assert args.worktree is True
    assert args.prompt == "hi"


def test_parser_flag_with_name_and_eq_form():
    assert _build_parser().parse_args(["--worktree", "fix-auth"]).worktree == "fix-auth"
    assert _build_parser().parse_args(["--worktree=fix-auth"]).worktree == "fix-auth"
    assert _build_parser().parse_args(["-w", "#123", "-p"]).worktree == "#123"


def test_parser_default_is_off():
    assert _build_parser().parse_args([]).worktree is None


# ── _maybe_create_worktree ───────────────────────────────────────────────────

def test_maybe_create_returns_none_without_flag():
    assert _maybe_create_worktree(SimpleNamespace(worktree=None)) is None


def test_maybe_create_reports_failure_outside_git_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = _maybe_create_worktree(SimpleNamespace(worktree=True))
    assert result is _WORKTREE_FAILED
    err = capsys.readouterr().err
    assert "Error creating worktree" in err
    assert "git repository" in err


# ── env hygiene (critic B1: nested sessions must never adopt the outer one) ──

def test_cli_main_strips_inherited_worktree_env():
    from src.cli import main

    inherited = {
        ENV_PATH: "/outer/.clawcodex/worktrees/x",
        "CLAWCODEX_WORKTREE_NAME": "x",
        "CLAWCODEX_WORKTREE_BRANCH": "worktree-x",
        "CLAWCODEX_WORKTREE_ORIGINAL_CWD": "/outer",
        "CLAWCODEX_WORKTREE_REPO_ROOT": "/outer",
        ENV_OWNER_PID: "12345",
    }
    with patch.dict(os.environ, inherited):
        # --version is the cheapest full pass through main()'s entry block.
        with patch("sys.argv", ["clawcodex", "--version"]):
            main()
        for key in inherited:
            assert key not in os.environ, f"{key} leaked through cli entry"


# ── launcher env plumbing ────────────────────────────────────────────────────

def _launcher_args(worktree: WorktreeSession | None) -> SimpleNamespace:
    return SimpleNamespace(
        provider=None, model=None, permission_mode="default",
        is_bypass_available=False, worktree_session=worktree,
    )


def test_child_env_carries_worktree_block_with_owner_pid():
    session = WorktreeSession(
        worktree_name="feat", worktree_path="/r/.clawcodex/worktrees/feat",
        worktree_branch="worktree-feat", original_cwd="/r", repo_root="/r",
    )
    env = _child_env(_launcher_args(session))
    assert env[ENV_PATH] == "/r/.clawcodex/worktrees/feat"
    assert env["CLAWCODEX_WORKTREE_NAME"] == "feat"
    assert env["CLAWCODEX_WORKTREE_BRANCH"] == "worktree-feat"
    assert env["CLAWCODEX_WORKTREE_ORIGINAL_CWD"] == "/r"
    assert env["CLAWCODEX_WORKTREE_REPO_ROOT"] == "/r"
    assert env[ENV_OWNER_PID] == str(os.getpid())


def test_child_env_has_no_worktree_block_without_session():
    env = _child_env(_launcher_args(None))
    assert not any(k.startswith("CLAWCODEX_WORKTREE_") for k in env)


# ── headless keep note ───────────────────────────────────────────────────────

def _headless_args(**overrides):
    base = dict(
        prompt="hi", output_format="text", input_format="text",
        include_partial_messages=False, max_turns=1, model=None,
        fallback_model=None, effort=None, provider=None, allowed_tools=None,
        disallowed_tools=None, verbose=False,
        dangerously_skip_permissions=False,
        _resolved_permission_mode="default",
        _resolved_is_bypass_available=False,
        worktree=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_print_mode_chdirs_into_worktree_and_prints_keep_note(tmp_path, monkeypatch, capsys):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=git_env)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=repo, check=True, env=git_env)
    monkeypatch.chdir(repo)

    from src import cli

    seen = {}

    def fake_run_headless(options):
        seen["cwd"] = os.getcwd()
        return 0

    with patch("src.entrypoints.headless.run_headless", fake_run_headless):
        rc = cli._run_print_mode(_headless_args(worktree="headless-wt"))

    assert rc == 0
    expected_wt = str(repo / ".clawcodex" / "worktrees" / "headless-wt")
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(expected_wt)
    err = capsys.readouterr().err
    assert "Worktree kept at" in err
    assert "headless-wt" in err
    assert os.path.isdir(expected_wt)  # headless always keeps


def test_print_mode_keep_note_survives_run_errors(tmp_path, monkeypatch, capsys):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=git_env)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=repo, check=True, env=git_env)
    monkeypatch.chdir(repo)

    from src import cli

    def exploding_run_headless(options):
        raise RuntimeError("boom")

    with patch("src.entrypoints.headless.run_headless", exploding_run_headless):
        try:
            cli._run_print_mode(_headless_args(worktree="errpath"))
        except RuntimeError:
            pass

    assert "Worktree kept at" in capsys.readouterr().err
