"""Unit tests for src/utils/worktree_session.py (the --worktree feature core).

Temp-repo tests drive real git — creation, fast-resume, orphan-dir rejection,
the lost-commit matrix (critic-mandated: the --exclude short-name form), and
cleanup. No network: repos are remote-less unless a test adds a local remote.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.utils.worktree_session import (
    ENV_OWNER_PID,
    ENV_PATH,
    ENV_PREFIX,
    WorktreeError,
    WorktreeSession,
    cleanup_worktree,
    create_session_from_cli_option,
    create_worktree_for_session,
    find_canonical_git_root,
    flatten_slug,
    generate_worktree_slug,
    keep_message,
    parse_pr_reference,
    removal_message,
    strip_worktree_env,
    validate_worktree_slug,
    worktree_branch_name,
    worktree_changes,
    worktree_path_for,
    worktrees_dir,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return result.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A remote-less repo with one initial commit (empty repos fail base
    resolution by design — HEAD is unresolvable)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")
    return root


# ── slug validation ──────────────────────────────────────────────────────────

def test_validate_slug_accepts_ts_allowlist():
    for slug in ("feature", "user/feature", "a.b_c-d", "pr-123", "A" * 64):
        validate_worktree_slug(slug)


@pytest.mark.parametrize("slug", [
    "../escape", "..", ".", "a/../b", "/abs", "a//b", "a/", "/",
    "spaces bad", "semi;colon", "a" * 65, "back\\slash", "tilde~",
])
def test_validate_slug_rejects_escapes_and_bad_chars(slug):
    with pytest.raises(WorktreeError):
        validate_worktree_slug(slug)


def test_flatten_slug_is_injective_over_allowed_alphabet():
    # '+' is not in the slug allowlist, so '/'→'+' cannot collide with a
    # literal slug (TS parity rationale).
    assert flatten_slug("user/feature") == "user+feature"
    assert worktree_branch_name("user/feature") == "worktree-user+feature"
    with pytest.raises(WorktreeError):
        validate_worktree_slug("user+feature")


def test_paths_flatten_nested_slugs(repo):
    path = worktree_path_for(str(repo), "user/feature")
    assert path == str(repo / ".clawcodex" / "worktrees" / "user+feature")
    assert worktrees_dir(str(repo)) == str(repo / ".clawcodex" / "worktrees")


# ── PR references ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("#123", 123),
    ("https://github.com/owner/repo/pull/45", 45),
    ("https://github.com/owner/repo/pull/45/", 45),
    ("https://github.com/owner/repo/pull/45?tab=files", 45),
    ("https://ghe.example.com/o/r/pull/7#discussion", 7),
    ("http://github.com/o/r/pull/9", 9),
    ("123", None),
    ("#12a", None),
    ("https://gitlab.com/o/r/-/merge_requests/3", None),
    ("pull/45", None),
])
def test_parse_pr_reference(text, expected):
    assert parse_pr_reference(text) == expected


# ── env round-trip + hygiene ─────────────────────────────────────────────────

def test_env_round_trip_and_owner_pid():
    session = WorktreeSession(
        worktree_name="x", worktree_path="/r/.clawcodex/worktrees/x",
        worktree_branch="worktree-x", original_cwd="/r", repo_root="/r",
    )
    env = session.to_env()
    assert env[ENV_OWNER_PID] == str(os.getpid())
    assert WorktreeSession.from_env(env) == session


def test_from_env_requires_every_var():
    env = WorktreeSession(
        worktree_name="x", worktree_path="/p", worktree_branch="b",
        original_cwd="/r", repo_root="/r",
    ).to_env()
    for key in list(env):
        if key == ENV_OWNER_PID:
            continue  # owner pid is a client-side (TUI) gate, not required here
        partial = {k: v for k, v in env.items() if k != key}
        assert WorktreeSession.from_env(partial) is None


def test_strip_worktree_env_removes_inherited_block():
    # Nested-session hygiene: a clawcodex spawned inside a --worktree session
    # inherits the block; cli.main strips it so the nested TUI can never adopt
    # (and clean-exit-delete) the outer session's worktree.
    env = {ENV_PATH: "/outer", f"{ENV_PREFIX}NAME": "outer", "OTHER": "keep"}
    strip_worktree_env(env)
    assert env == {"OTHER": "keep"}


# ── creation / resume ────────────────────────────────────────────────────────

def test_create_worktree_for_session_creates_dir_and_branch(repo):
    session = create_worktree_for_session("feat-x", cwd=str(repo))
    assert session.worktree_path == str(repo / ".clawcodex" / "worktrees" / "feat-x")
    assert session.worktree_branch == "worktree-feat-x"
    assert session.repo_root == str(repo)
    assert (Path(session.worktree_path) / ".git").is_file()
    assert (Path(session.worktree_path) / "README.md").read_text() == "hello\n"
    assert "worktree-feat-x" in _git(repo, "branch", "--list", "worktree-feat-x")


def test_create_from_subdirectory_lands_in_repo_root(repo):
    sub = repo / "src"
    sub.mkdir()
    session = create_worktree_for_session("from-sub", cwd=str(sub))
    assert session.repo_root == str(repo)
    assert session.worktree_path.startswith(str(repo / ".clawcodex"))
    assert session.original_cwd == str(sub)


def test_resume_existing_worktree_preserves_work(repo):
    first = create_worktree_for_session("keeper", cwd=str(repo))
    marker = Path(first.worktree_path) / "wip.txt"
    marker.write_text("in progress\n")

    second = create_worktree_for_session("keeper", cwd=str(repo))
    assert second.worktree_path == first.worktree_path
    assert marker.read_text() == "in progress\n"  # resume, not recreate


def test_creating_inside_a_worktree_resolves_to_main_repo(repo):
    outer = create_worktree_for_session("outer", cwd=str(repo))
    inner = create_worktree_for_session("inner", cwd=outer.worktree_path)
    # Lands in the MAIN repo's .clawcodex/worktrees, not nested inside `outer`.
    assert inner.worktree_path == str(repo / ".clawcodex" / "worktrees" / "inner")


def test_orphaned_plain_dir_is_rejected_not_adopted(repo):
    """Critic B2: a plain directory at the worktree path must NOT fast-resume
    (bare rev-parse walks up and reports the MAIN repo's HEAD) and must NOT be
    auto-deleted — creation fails loud with a prune hint."""
    orphan = repo / ".clawcodex" / "worktrees" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "leftover.txt").write_text("precious\n")

    with pytest.raises(WorktreeError) as excinfo:
        create_worktree_for_session("orphan", cwd=str(repo))
    assert "git worktree prune" in str(excinfo.value)
    assert (orphan / "leftover.txt").read_text() == "precious\n"  # untouched


def test_dash_b_resets_orphan_branch(repo):
    """A leftover branch without a worktree dir is reset by `-B` (TS parity)."""
    _git(repo, "branch", "worktree-ghost")
    session = create_worktree_for_session("ghost", cwd=str(repo))
    assert (Path(session.worktree_path) / ".git").is_file()


def test_base_falls_back_to_head_without_origin(repo):
    """Remote-less repo: base resolution ends at HEAD (no fetch, no crash)."""
    session = create_worktree_for_session("no-origin", cwd=str(repo))
    wt_head = _git(Path(session.worktree_path), "rev-parse", "HEAD")
    assert wt_head == _git(repo, "rev-parse", "HEAD")


def test_not_a_git_repo_raises_clean_error(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError) as excinfo:
        create_worktree_for_session("x", cwd=str(plain))
    assert "git repository" in str(excinfo.value)


def test_generated_slug_is_valid_and_fresh(repo):
    slug = generate_worktree_slug(str(repo))
    validate_worktree_slug(slug)
    assert not os.path.exists(worktree_path_for(str(repo), slug))


def test_cli_option_true_generates_name_and_pr_ref_maps_to_slug(repo, monkeypatch):
    monkeypatch.chdir(repo)
    session = create_session_from_cli_option(True)
    assert session.worktree_name  # generated
    # PR refs would fetch pull/<n>/head — no remote here, so expect the
    # fetch-failure error, proving the option routed into PR mode.
    with pytest.raises(WorktreeError) as excinfo:
        create_session_from_cli_option("#12345")
    assert "PR #12345" in str(excinfo.value)


# ── canonical root ───────────────────────────────────────────────────────────

def test_find_canonical_git_root(repo):
    assert find_canonical_git_root(str(repo)) == str(repo)
    sub = repo / "deep" / "er"
    sub.mkdir(parents=True)
    assert find_canonical_git_root(str(sub)) == str(repo)
    session = create_worktree_for_session("rooty", cwd=str(repo))
    assert find_canonical_git_root(session.worktree_path) == str(repo)
    assert find_canonical_git_root("/") is None


# ── change measurement (the lost-commit matrix, critic B4) ───────────────────

def test_changes_clean_worktree_is_clean_in_remote_less_repo(repo):
    session = create_worktree_for_session("clean", cwd=str(repo))
    changes = worktree_changes(session)
    assert changes.git_ok is True
    assert (changes.dirty_files, changes.commits) == (0, 0)
    assert changes.is_clean


def test_changes_counts_dirty_files(repo):
    session = create_worktree_for_session("dirty", cwd=str(repo))
    (Path(session.worktree_path) / "a.txt").write_text("a")
    (Path(session.worktree_path) / "b.txt").write_text("b")
    changes = worktree_changes(session)
    assert changes.dirty_files == 2
    assert changes.commits == 0
    assert not changes.is_clean


def test_changes_counts_unmerged_commit_as_lost(repo):
    """The critical case that catches a wrong --exclude pattern form: a
    worktree-only commit must count as 1 (refs/heads/<branch> as the exclude
    pattern silently matches nothing and reads 0 → silent data loss)."""
    session = create_worktree_for_session("unmerged", cwd=str(repo))
    wt = Path(session.worktree_path)
    (wt / "new.txt").write_text("x")
    _git(wt, "add", "new.txt")
    _git(wt, "commit", "-q", "-m", "wip")
    changes = worktree_changes(session)
    assert changes.commits == 1
    assert not changes.is_clean


def test_changes_merged_back_commit_is_not_lost(repo):
    """Better than TS: work merged into main no longer warns on remove."""
    session = create_worktree_for_session("merged", cwd=str(repo))
    wt = Path(session.worktree_path)
    (wt / "done.txt").write_text("x")
    _git(wt, "add", "done.txt")
    _git(wt, "commit", "-q", "-m", "done")
    _git(repo, "merge", "-q", session.worktree_branch)
    changes = worktree_changes(session)
    assert (changes.dirty_files, changes.commits) == (0, 0)
    assert changes.is_clean


def test_changes_resumed_worktree_still_counts_prior_commit(repo):
    """Critic M1: TS's base..HEAD counts 0 after a resume (base is
    HEAD-at-resume) and silently discards the prior session's commit; the
    lost-set count keeps protecting it."""
    first = create_worktree_for_session("resumed", cwd=str(repo))
    wt = Path(first.worktree_path)
    (wt / "s1.txt").write_text("x")
    _git(wt, "add", "s1.txt")
    _git(wt, "commit", "-q", "-m", "session-1 work")

    second = create_worktree_for_session("resumed", cwd=str(repo))  # resume
    changes = worktree_changes(second)
    assert changes.commits == 1
    assert not changes.is_clean


def test_changes_fails_closed_when_git_fails():
    ghost = WorktreeSession(
        worktree_name="gone", worktree_path="/nonexistent/worktree",
        worktree_branch="worktree-gone", original_cwd="/", repo_root="/",
    )
    changes = worktree_changes(ghost)
    assert changes.git_ok is False
    assert not changes.is_clean


# ── cleanup ──────────────────────────────────────────────────────────────────

def test_cleanup_removes_dir_and_branch(repo):
    session = create_worktree_for_session("byebye", cwd=str(repo))
    ok, error = cleanup_worktree(session)
    assert ok, error
    assert not os.path.exists(session.worktree_path)
    assert _git(repo, "branch", "--list", "worktree-byebye") == ""


def test_cleanup_reports_failure(repo):
    session = create_worktree_for_session("failing", cwd=str(repo))
    broken = WorktreeSession(
        worktree_name="failing", worktree_path="/nonexistent/nowhere",
        worktree_branch=session.worktree_branch,
        original_cwd=session.original_cwd, repo_root=session.repo_root,
    )
    ok, error = cleanup_worktree(broken)
    assert not ok
    assert error


# ── post-creation setup ──────────────────────────────────────────────────────

def test_settings_local_json_copies_clawcodex_tier_only(repo):
    # Only clawcodex's own project tier propagates. The real Claude Code
    # harness's .claude/settings.local.json (foreign permission grants) is
    # deliberately NOT copied since the directory rebrand.
    for dirname in (".clawcodex", ".claude"):
        d = repo / dirname
        d.mkdir(exist_ok=True)
        (d / "settings.local.json").write_text('{"permissions": {}}')
    session = create_worktree_for_session("settings", cwd=str(repo))
    copied = Path(session.worktree_path) / ".clawcodex" / "settings.local.json"
    assert copied.read_text() == '{"permissions": {}}'
    assert not (Path(session.worktree_path) / ".claude" / "settings.local.json").exists()


def test_legacy_claude_worktree_is_resumed_and_removable(repo):
    # A pre-rebrand worktree under .claude/worktrees/ (git-registered at its
    # absolute path) must be resumed in place — creating a fresh tree would
    # also fail on the branch being checked out there — and the exit-dialog
    # removal must accept the legacy root.
    legacy_dir = repo / ".claude" / "worktrees"
    legacy_dir.mkdir(parents=True)
    legacy_path = legacy_dir / "old-task"
    subprocess.run(
        ["git", "worktree", "add", "-B", "worktree-old-task", str(legacy_path), "HEAD"],
        cwd=repo, check=True, capture_output=True,
    )

    session = create_worktree_for_session("old-task", cwd=str(repo))
    assert session.worktree_path == str(legacy_path)

    ok, error = cleanup_worktree(session)
    assert ok, error
    assert not legacy_path.exists()


def test_worktreeinclude_copies_matched_gitignored_files(repo):
    (repo / ".gitignore").write_text(".env\nbuild/\n")
    (repo / ".worktreeinclude").write_text("# secrets\n.env\n")
    (repo / ".env").write_text("SECRET=1\n")
    build = repo / "build"
    build.mkdir()
    (build / "out.bin").write_text("binary")
    _git(repo, "add", ".gitignore", ".worktreeinclude")
    _git(repo, "commit", "-q", "-m", "ignore rules")

    session = create_worktree_for_session("env-carry", cwd=str(repo))
    wt = Path(session.worktree_path)
    assert (wt / ".env").read_text() == "SECRET=1\n"          # matched → copied
    assert not (wt / "build" / "out.bin").exists()             # ignored, unmatched


def test_worktreeinclude_expands_collapsed_dir_for_targeted_pattern(repo):
    (repo / ".gitignore").write_text("config/\n")
    (repo / ".worktreeinclude").write_text("config/secrets/api.key\n")
    secrets = repo / "config" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "api.key").write_text("k")
    (repo / "config" / "other.txt").write_text("no")
    _git(repo, "add", ".gitignore", ".worktreeinclude")
    _git(repo, "commit", "-q", "-m", "rules")

    session = create_worktree_for_session("collapsed", cwd=str(repo))
    wt = Path(session.worktree_path)
    assert (wt / "config" / "secrets" / "api.key").read_text() == "k"
    assert not (wt / "config" / "other.txt").exists()


# ── messages ─────────────────────────────────────────────────────────────────

def test_message_wording_matrix(repo):
    session = WorktreeSession(
        worktree_name="m", worktree_path="/p", worktree_branch="worktree-m",
        original_cwd="/r", repo_root="/r",
    )
    from src.utils.worktree_session import WorktreeChanges

    assert keep_message(session) == (
        "Worktree kept. Your work is saved at /p on branch worktree-m"
    )
    ok = lambda d, c: WorktreeChanges(git_ok=True, dirty_files=d, commits=c)  # noqa: E731
    assert removal_message(session, ok(0, 0)) == "Worktree removed (no changes)"
    assert removal_message(session, ok(2, 0)) == (
        "Worktree removed. Uncommitted changes were discarded."
    )
    assert removal_message(session, ok(0, 1)) == (
        "Worktree removed. 1 commit on worktree-m was discarded."
    )
    assert removal_message(session, ok(0, 3)) == (
        "Worktree removed. 3 commits on worktree-m were discarded."
    )
    assert removal_message(session, ok(2, 3)) == (
        "Worktree removed. 3 commits and uncommitted changes were discarded."
    )
    assert removal_message(
        session, WorktreeChanges(git_ok=False, dirty_files=0, commits=0)
    ) == "Worktree removed."
