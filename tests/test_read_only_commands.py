"""Tests for the read-only Bash auto-allow gate (loosen-permissions).

Port-parity targets: typescript/src/tools/BashTool/readOnlyValidation.ts
(isCommandReadOnly / checkReadOnlyConstraints) and the shared validateFlags
loop. The gate is what lets ``ls`` / ``git status`` / ``grep`` run with NO
prompt and NO rule in default mode, so its REFUSALS are the security surface:
every "refused" case here is a command that must keep prompting.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.permissions.read_only_commands import (
    check_read_only_constraints,
    contains_unquoted_expansion,
    is_command_read_only,
    is_current_directory_bare_git_repo,
)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self.tmp.name).resolve())
        # A normal .git so the bare-repo guard doesn't trip.
        os.makedirs(os.path.join(self.root, ".git"), exist_ok=True)
        Path(self.root, ".git", "HEAD").write_text("ref: refs/heads/main\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ro(self, command: str) -> bool:
        return check_read_only_constraints(
            command, cwd=self.root, allowed_roots=[self.root]
        )


class TestReadOnlyAllowed(_Base):
    def test_plain_listing_and_reading(self) -> None:
        for cmd in (
            "ls",
            "ls -la",
            "ls -la src",
            "pwd",
            "cat README.txt",
            "cat f.txt 2>&1",
            "head -20 f.py",
            "tail -n 50 log.txt",
            "wc -l f.py",
            "tree -L 2",
            "diff a.txt b.txt",
            "which python3",
            "readlink f",
            "du -sh .",
            "uname -a",
            "whoami",
            "true",
            "sleep 2",
        ):
            self.assertTrue(self.ro(cmd), cmd)

    def test_git_read_only_subcommands(self) -> None:
        for cmd in (
            "git status",
            "git log --oneline -20",
            "git diff --stat",
            "git diff HEAD~1",
            "git branch",
            "git show HEAD",
            "git blame f.py",
        ):
            self.assertTrue(self.ro(cmd), cmd)

    def test_search_tools(self) -> None:
        for cmd in (
            "grep -rn pattern .",
            "grep -A20 foo f.txt",
            "rg -n pattern src",
            "find . -name test_foo.py",
        ):
            self.assertTrue(self.ro(cmd), cmd)

    def test_compound_all_read_only(self) -> None:
        self.assertTrue(self.ro("git status && ls -la"))
        self.assertTrue(self.ro("cat f.txt | head -5"))
        self.assertTrue(self.ro("git log --oneline | head -20"))
        self.assertTrue(self.ro("cd src && ls"))

    def test_relative_paths_inside_roots(self) -> None:
        self.assertTrue(self.ro("cat src/permissions/check.py"))
        self.assertTrue(self.ro(f"ls {self.root}"))


class TestReadOnlyRefused(_Base):
    def test_write_and_exec_commands(self) -> None:
        for cmd in (
            "rm -rf build",
            "touch x",
            "mkdir y",
            "mv a b",
            "cp a b",
            "python x.py",
            "pytest -q",
            "npm run build",
            "pip install requests",
            "curl https://example.com",
            "tar -xf a.tar",
        ):
            self.assertFalse(self.ro(cmd), cmd)

    def test_git_mutating_subcommands(self) -> None:
        for cmd in (
            "git push",
            "git push --force origin main",
            "git commit -m x",
            "git checkout -b feat",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "git rebase main",
        ):
            self.assertFalse(self.ro(cmd), cmd)

    def test_write_flags_on_read_only_binaries(self) -> None:
        # Flag-aware refusals — exactly what the crude first-token lists miss.
        for cmd in (
            "sort -o out.txt in.txt",
            "date -s '2020-01-01'",
            "sed -i s/a/b/ f.txt",
            "tree -o out.html",
            "find . -delete",
            "find . -exec rm {} ;",
            "git -c core.fsmonitor=/tmp/evil status",
            "git --exec-path=/tmp/evil status",
        ):
            self.assertFalse(self.ro(cmd), cmd)

    def test_redirects_and_operators(self) -> None:
        for cmd in (
            "ls > files.txt",
            "cat a >> b",
            "echo hi > f",
            "grep x f 2>/dev/null",
            "sort < in.txt",
        ):
            self.assertFalse(self.ro(cmd), cmd)

    def test_expansion_refused(self) -> None:
        # Globs/vars can expand into flags the validators never saw.
        for cmd in ("ls *.py", "cat $FILE", "grep x $(ls)", "echo `date`"):
            self.assertFalse(self.ro(cmd), cmd)

    def test_quoted_globs_are_fine(self) -> None:
        self.assertFalse(contains_unquoted_expansion("grep 'a*b' f.txt"))
        self.assertTrue(contains_unquoted_expansion("grep a*b f.txt"))
        self.assertTrue(contains_unquoted_expansion('echo "$HOME"'))
        self.assertFalse(contains_unquoted_expansion("echo '$HOME'"))

    def test_compound_with_non_read_only_leg(self) -> None:
        self.assertFalse(self.ro("git status && git push"))
        self.assertFalse(self.ro("ls && rm -rf x"))
        self.assertFalse(self.ro("cat f | xargs rm"))

    def test_cd_git_and_multi_cd_guards(self) -> None:
        self.assertFalse(self.ro("cd sub && git status"))
        self.assertFalse(self.ro("pushd sub && git log"))
        self.assertFalse(self.ro("cd a; cd b"))

    def test_paths_outside_roots_refused(self) -> None:
        for cmd in (
            "cat /etc/passwd",
            "ls ~/",
            "cat ../outside.txt",
            "cd /tmp",
            "head -5 /var/log/system.log",
        ):
            self.assertFalse(self.ro(cmd), cmd)

    def test_bare_shells_and_wrappers(self) -> None:
        for cmd in ("bash -c ls", "sh script.sh", "env ls", "xargs rm",
                    "sudo ls", "eval ls"):
            self.assertFalse(self.ro(cmd), cmd)

    def test_xargs_rI_parser_differential_refused(self) -> None:
        # GNU getopt bundling: `-rI echo sh -c id` runs `sh -c id`.
        self.assertFalse(is_command_read_only("xargs -rI echo sh -c id"))

    def test_ps_bsd_e_modifier_refused(self) -> None:
        self.assertFalse(is_command_read_only("ps axe"))
        self.assertTrue(is_command_read_only("ps aux"))


class TestBareRepoGuard(unittest.TestCase):
    def test_bare_repo_indicators_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve())
            # No .git; plant bare-repo indicators.
            os.makedirs(os.path.join(root, "objects"))
            os.makedirs(os.path.join(root, "refs"))
            Path(root, "HEAD").write_text("ref: refs/heads/main\n")
            self.assertTrue(is_current_directory_bare_git_repo(root))
            self.assertFalse(
                check_read_only_constraints(
                    "git status", cwd=root, allowed_roots=[root]
                )
            )
            # Non-git read-only commands are unaffected by the guard.
            self.assertTrue(
                check_read_only_constraints(
                    "ls -la", cwd=root, allowed_roots=[root]
                )
            )

    def test_normal_repo_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve())
            os.makedirs(os.path.join(root, ".git"))
            Path(root, ".git", "HEAD").write_text("ref: refs/heads/main\n")
            self.assertFalse(is_current_directory_bare_git_repo(root))

    def test_worktree_gitfile_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp).resolve())
            Path(root, ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
            self.assertFalse(is_current_directory_bare_git_repo(root))


if __name__ == "__main__":
    unittest.main()
