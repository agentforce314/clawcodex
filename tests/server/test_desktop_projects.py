"""Tests for the desktop ``projects.tree`` grouping (src/server/desktop_projects)."""

from __future__ import annotations

from src.server.desktop_projects import (
    NO_PROJECT_ID,
    build_project_tree,
    canonical_workspace_path,
)


def _row(sid: str, cwd: str | None, last: float = 0.0) -> dict:
    return {"id": sid, "cwd": cwd, "last_active": last, "title": sid}


def _workspace_path(cwd: str) -> str:
    return cwd


def test_groups_sessions_by_repo_root_into_auto_projects():
    rows = [_row("a", "/repo/src", 3.0), _row("b", "/repo", 2.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo" if cwd.startswith("/repo") else None,
        worktrees_of=lambda root: ["/repo"],
        workspace_path_of=_workspace_path,
    )
    assert len(tree["projects"]) == 1
    proj = tree["projects"][0]
    assert proj["id"] == "/repo" and proj["isAuto"] is True
    assert proj["sessionCount"] == 2
    # Both sessions are in the main checkout → the single home/main lane.
    lanes = proj["repos"][0]["groups"]
    assert len(lanes) == 1 and lanes[0]["isMain"] is True
    assert {s["id"] for s in lanes[0]["sessions"]} == {"a", "b"}


def test_linked_worktree_session_gets_its_own_lane():
    rows = [_row("main", "/repo", 1.0), _row("feat", "/repo/.worktrees/feature", 2.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo",
        worktrees_of=lambda root: ["/repo", "/repo/.worktrees/feature"],
        workspace_path_of=_workspace_path,
    )
    lanes = tree["projects"][0]["repos"][0]["groups"]
    main = [g for g in lanes if g["isMain"]]
    linked = [g for g in lanes if not g["isMain"]]
    assert len(main) == 1 and len(linked) == 1
    assert linked[0]["path"] == "/repo/.worktrees/feature"
    assert [s["id"] for s in linked[0]["sessions"]] == ["feat"]


def test_non_repo_sessions_group_by_workspace_directory():
    rows = [
        _row("scratch-old", "/tmp/scratch", 1.0),
        _row("scratch-new", "/tmp/scratch", 3.0),
        _row("other", "/var/tmp/other", 2.0),
    ]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: None,  # nothing is a repo
        worktrees_of=lambda root: [],
        workspace_path_of=_workspace_path,
    )

    assert [project["id"] for project in tree["projects"]] == [
        "/tmp/scratch",
        "/var/tmp/other",
    ]
    scratch = tree["projects"][0]
    assert scratch["label"] == "scratch"
    assert scratch["path"] == "/tmp/scratch"
    assert scratch["isAuto"] is True
    assert scratch.get("isNoProject") is None
    assert {row["id"] for row in scratch["repos"][0]["groups"][0]["sessions"]} == {
        "scratch-old",
        "scratch-new",
    }


def test_cwdless_sessions_fall_into_home_bucket():
    rows = [_row("x", None), _row("y", "")]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: None,
        worktrees_of=lambda root: [],
        workspace_path_of=_workspace_path,
    )

    assert len(tree["projects"]) == 1
    home = tree["projects"][0]
    assert home["id"] == NO_PROJECT_ID and home["isNoProject"] is True
    assert home["sessionCount"] == 2


def test_active_non_repo_workspace_uses_its_directory_id():
    tree = build_project_tree(
        [_row("x", "/tmp/scratch")],
        repo_root_of=lambda cwd: None,
        worktrees_of=lambda root: [],
        workspace_path_of=_workspace_path,
        active_cwd="/tmp/scratch",
    )

    assert tree["active_id"] == "/tmp/scratch"


def test_windows_paths_group_case_and_separator_insensitively():
    # git emits forward-slash roots; session cwds are backslash + can differ in
    # drive case. They must still land in ONE repo, one home lane.
    rows = [
        _row("a", "C:\\Repo\\src", 1.0),
        _row("b", "c:/repo", 2.0),
    ]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "C:/Repo",
        worktrees_of=lambda root: ["C:/Repo"],
        workspace_path_of=_workspace_path,
    )
    assert len(tree["projects"]) == 1
    lanes = tree["projects"][0]["repos"][0]["groups"]
    assert len(lanes) == 1 and lanes[0]["isMain"] is True
    assert {s["id"] for s in lanes[0]["sessions"]} == {"a", "b"}


def test_active_id_and_scoped_ids():
    rows = [_row("a", "/repo", 1.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo",
        worktrees_of=lambda root: ["/repo"],
        workspace_path_of=_workspace_path,
        active_cwd="/repo",
    )
    assert tree["active_id"] == "/repo"
    assert tree["scoped_session_ids"] == ["a"]


def test_workspace_path_resolver_canonicalizes_and_rejects_invalid_paths(tmp_path):
    workspace = tmp_path / "parent" / "workspace"
    workspace.mkdir(parents=True)
    alias = workspace / ".." / "workspace"

    assert canonical_workspace_path(str(alias)) == str(workspace.resolve())
    assert canonical_workspace_path(str(tmp_path / "missing")) is None


def test_canonical_workspace_aliases_share_one_project(tmp_path):
    workspace = tmp_path / "parent" / "workspace"
    workspace.mkdir(parents=True)
    alias = workspace / ".." / "workspace"
    rows = [_row("canonical", str(workspace)), _row("alias", str(alias))]

    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: None,
        worktrees_of=lambda root: [],
        workspace_path_of=canonical_workspace_path,
        active_cwd=str(alias),
    )

    assert len(tree["projects"]) == 1
    project = tree["projects"][0]
    assert project["id"] == str(workspace.resolve())
    assert project["label"] == "workspace"
    assert project["path"] == str(workspace.resolve())
    assert {row["id"] for row in project["repos"][0]["groups"][0]["sessions"]} == {
        "canonical",
        "alias",
    }
    assert tree["active_id"] == str(workspace.resolve())


def test_unresolved_cwd_falls_into_home_bucket(tmp_path):
    missing = tmp_path / "missing"
    tree = build_project_tree(
        [_row("gone", str(missing))],
        repo_root_of=lambda cwd: None,
        worktrees_of=lambda root: [],
        workspace_path_of=canonical_workspace_path,
    )

    assert [project["id"] for project in tree["projects"]] == [NO_PROJECT_ID]


# ─── ProbeCache ──────────────────────────────────────────────────────────────


def test_probe_cache_answers_within_its_ttl_and_forgets_after():
    from src.server.desktop_projects import ProbeCache

    now = [100.0]
    cache = ProbeCache(ttl_s=10.0, worktree_ttl_s=2.0, clock=lambda: now[0])

    assert cache.has_repo_root("/a") is False
    cache.set_repo_root("/a", "/repo")
    cache.set_repo_root("/b", None)  # "not a repo" is an answer too
    cache.set_worktrees("/repo", ["/repo", "/repo/.wt/x"])

    assert cache.has_repo_root("/a") and cache.repo_root("/a") == "/repo"
    assert cache.has_repo_root("/b") and cache.repo_root("/b") is None
    assert cache.worktrees("/repo") == ["/repo", "/repo/.wt/x"]

    now[0] += 3.0
    assert cache.worktrees("/repo") is None  # worktree lists expire sooner
    assert cache.has_repo_root("/a")
    now[0] += 8.0
    assert cache.has_repo_root("/a") is False


def test_probe_cache_forget_worktrees_drops_one_repo():
    from src.server.desktop_projects import ProbeCache

    cache = ProbeCache()
    cache.set_worktrees("/r1", ["/r1"])
    cache.set_worktrees("/r2", ["/r2"])
    cache.forget_worktrees("/r1")
    assert cache.worktrees("/r1") is None
    assert cache.worktrees("/r2") == ["/r2"]
