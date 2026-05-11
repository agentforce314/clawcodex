"""ch07 / M5: read-only Bash classifier — git subcommand discipline.

Locks the classification of git subcommands. `git push` / `git commit`
/ `git reset` are NOT read-only and must not be batched concurrently
(would race on .git/index.lock). `git status` / `git log` / etc. are
read-only and safe to parallelize.

Also locks the conservative classification of `git stash *` and
`git worktree *` — their `list` forms are technically read-only, but
the plan classifies them as NOT read-only to avoid parsing
sub-subcommands. A contributor who later "fixes" this needs to update
the test deliberately.
"""
from __future__ import annotations

import pytest

from src.tool_system.tools.bash.read_only_validation import is_command_read_only


@pytest.mark.parametrize("cmd", [
    "git push",
    "git push --force origin main",
    "git commit -m hi",
    "git commit --amend",
    "git reset --hard",
    "git rebase main",
    "git clean -fd",
    "git merge feature",
    "git cherry-pick HEAD~1",
    "git stash",
    "git stash push",
    "git stash list",        # conservatively NOT marked safe
    "git worktree list",     # conservatively NOT marked safe
    "git worktree add ../x", # actually mutating
    "git",                   # bare git → ambiguous → not safe
])
def test_git_write_subcommands_are_not_read_only(cmd: str) -> None:
    assert is_command_read_only(cmd) is False, f"{cmd!r} must not be read-only"


@pytest.mark.parametrize("cmd", [
    "git status",
    "git diff",
    "git diff --stat HEAD~1",
    "git log --oneline -5",
    "git show HEAD",
    "git blame foo.py",
    "git branch",
    "git tag",
    "git describe",
    "git rev-parse HEAD",
    "git ls-files",
    "git ls-tree HEAD",
    "git config user.name",
    "git remote -v",
    "git shortlog",
    "git reflog",
    "git whatchanged",
])
def test_git_read_subcommands_are_read_only(cmd: str) -> None:
    assert is_command_read_only(cmd) is True, f"{cmd!r} must be read-only"


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat README.md",
    "grep foo bar.txt",
    "find . -name '*.py'",
    "cat README.md | grep foo",
    "ls | head",
])
def test_existing_read_only_commands_unaffected(cmd: str) -> None:
    """The git-subcommand change must not regress any non-git case."""
    assert is_command_read_only(cmd) is True


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "mkdir build",
    "ls && cat foo",           # compound — still rejected by metachar guard
    "echo $(whoami)",          # subshell — still rejected by metachar guard
    "cat foo; rm bar",         # semicolon — still rejected
])
def test_existing_non_read_only_commands_unaffected(cmd: str) -> None:
    assert is_command_read_only(cmd) is False
