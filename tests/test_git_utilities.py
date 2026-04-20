import os
import subprocess
import tempfile
import pytest

from src.utils.git import (
    CommitAttribution,
    DiffResult,
    FileStatus,
    Worktree,
    create_branch,
    get_commit_attribution,
    get_current_branch,
    get_default_branch,
    get_diff_against_branch,
    get_file_status,
    get_repo_root,
    get_session_diff,
    list_worktrees,
    _run_git,
    _run_git_ok,
)


@pytest.fixture
def git_repo(tmp_path):
    repo_dir = str(tmp_path / "test_repo")
    os.makedirs(repo_dir)
    subprocess.run(["git", "init", repo_dir], capture_output=True)
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.email", "test@test.com"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.name", "Test"],
        capture_output=True,
    )
    with open(os.path.join(repo_dir, "README.md"), "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "-C", repo_dir, "add", "."], capture_output=True)
    subprocess.run(
        ["git", "-C", repo_dir, "commit", "-m", "initial"],
        capture_output=True,
    )
    return repo_dir


class TestRunGit:
    def test_run_git_ok(self, git_repo):
        result = _run_git_ok(["rev-parse", "--is-inside-work-tree"], git_repo)
        assert result == "true"

    def test_run_git_failure(self):
        stdout, stderr, rc = _run_git(["rev-parse", "--is-inside-work-tree"], "/tmp")
        assert rc != 0

    def test_run_git_timeout(self):
        stdout, stderr, rc = _run_git(["log"], timeout=0.001)
        assert rc != 0 or stdout == ""


class TestGetRepoRoot:
    def test_valid_repo(self, git_repo):
        root = get_repo_root(git_repo)
        assert root is not None
        assert os.path.isdir(root)

    def test_not_a_repo(self):
        root = get_repo_root("/tmp")
        assert root is None


class TestGetCurrentBranch:
    def test_get_branch(self, git_repo):
        branch = get_current_branch(git_repo)
        assert branch in ("main", "master")


class TestGetDefaultBranch:
    def test_get_default(self, git_repo):
        default = get_default_branch(git_repo)
        assert default in ("main", "master")


class TestGetFileStatus:
    def test_clean_repo(self, git_repo):
        status = get_file_status(git_repo)
        assert status == []

    def test_modified_file(self, git_repo):
        with open(os.path.join(git_repo, "README.md"), "a") as f:
            f.write("more content\n")
        status = get_file_status(git_repo)
        assert len(status) >= 1
        assert any(s.path == "README.md" for s in status)

    def test_new_file(self, git_repo):
        with open(os.path.join(git_repo, "new.txt"), "w") as f:
            f.write("new file\n")
        status = get_file_status(git_repo)
        assert len(status) >= 1
        new_files = [s for s in status if s.path == "new.txt"]
        assert len(new_files) == 1
        assert new_files[0].is_added


class TestFileStatus:
    def test_is_modified(self):
        fs = FileStatus(path="file.txt", status="M")
        assert fs.is_modified is True
        assert fs.is_added is False
        assert fs.is_deleted is False

    def test_is_added(self):
        fs = FileStatus(path="file.txt", status="A")
        assert fs.is_added is True

    def test_is_deleted(self):
        fs = FileStatus(path="file.txt", status="D")
        assert fs.is_deleted is True

    def test_is_untracked(self):
        fs = FileStatus(path="file.txt", status="??")
        assert fs.is_added is True

    def test_is_renamed(self):
        fs = FileStatus(path="new.txt", status="R", original_path="old.txt")
        assert fs.is_renamed is True


class TestGetSessionDiff:
    def test_no_changes(self, git_repo):
        result = get_session_diff(git_repo)
        assert isinstance(result, DiffResult)
        assert result.diff_text == ""

    def test_with_changes(self, git_repo):
        with open(os.path.join(git_repo, "README.md"), "a") as f:
            f.write("added line\n")
        result = get_session_diff(git_repo)
        assert result.diff_text != ""


class TestCreateBranch:
    def test_create_and_checkout(self, git_repo):
        success = create_branch("feature-test", git_repo)
        assert success is True
        branch = get_current_branch(git_repo)
        assert branch == "feature-test"


class TestListWorktrees:
    def test_list_main(self, git_repo):
        worktrees = list_worktrees(git_repo)
        assert len(worktrees) >= 1
        assert worktrees[0].is_main is True


class TestDiffResult:
    def test_fields(self):
        dr = DiffResult(diff_text="diff output", files_changed=3, insertions=10, deletions=5)
        assert dr.files_changed == 3
        assert dr.insertions == 10
        assert dr.deletions == 5


class TestCommitAttribution:
    def test_fields(self):
        ca = CommitAttribution(
            path="file.txt",
            modified_by_claude=True,
            lines_added=10,
            lines_removed=3,
        )
        assert ca.modified_by_claude is True
        assert ca.modified_by_user is False
