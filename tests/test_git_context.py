"""Tests for src/context_system/git_context.py — WS-5 git context snapshot."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.context_system.git_context import (
    GitContextSnapshot,
    _get_default_branch,
    _git_cmd,
    clear_git_caches,
    collect_git_context,
    format_git_status,
    get_is_git,
)
from src.context_system.models import MAX_STATUS_CHARS


def _run(coro):
    return asyncio.run(coro)


def _init_git_repo(path: str) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, capture_output=True)
    # Create a file and commit
    (Path(path) / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True)


class TestGitCmd(unittest.TestCase):
    def test_valid_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            result = _git_cmd(["rev-parse", "--is-inside-work-tree"], tmp)
            self.assertEqual(result, "true")

    def test_invalid_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _git_cmd(["rev-parse", "--is-inside-work-tree"], tmp)
            self.assertEqual(result, "")

    def test_nonexistent_dir(self):
        result = _git_cmd(["status"], "/nonexistent/dir/path")
        self.assertEqual(result, "")


class TestGetIsGit(unittest.TestCase):
    def test_git_repo(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            result = get_is_git(tmp)
            self.assertTrue(result)
        clear_git_caches()

    def test_non_git_dir(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            result = get_is_git(tmp)
            self.assertFalse(result)
        clear_git_caches()


class TestGetDefaultBranch(unittest.TestCase):
    def test_with_main_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            # Default init should create main or master
            result = _get_default_branch(tmp)
            self.assertIn(result, ("main", "master"))


class TestCollectGitContext(unittest.TestCase):
    def test_git_repo(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ctx = _run(collect_git_context(tmp))
            self.assertTrue(ctx.available)
            self.assertIsNotNone(ctx.branch)
            self.assertIsNotNone(ctx.default_branch)
            self.assertIsNotNone(ctx.user_name)
            self.assertEqual(ctx.user_name, "Test User")
            self.assertIsNotNone(ctx.recent_commits)
            self.assertIn("Initial commit", ctx.recent_commits)
        clear_git_caches()

    def test_non_git_dir(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _run(collect_git_context(tmp))
            self.assertFalse(ctx.available)
            self.assertIsNotNone(ctx.error)
        clear_git_caches()

    def test_memoization(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ctx1 = _run(collect_git_context(tmp))
            ctx2 = _run(collect_git_context(tmp))
            # Same object from cache
            self.assertIs(ctx1, ctx2)
        clear_git_caches()

    def test_cache_clearing(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ctx1 = _run(collect_git_context(tmp))
            clear_git_caches()
            ctx2 = _run(collect_git_context(tmp))
            # Different objects after cache clear
            self.assertIsNot(ctx1, ctx2)
            self.assertTrue(ctx2.available)
        clear_git_caches()

    def test_status_with_changes(self):
        clear_git_caches()
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            # Create an untracked file
            (Path(tmp) / "new_file.py").write_text("print('hi')\n", encoding="utf-8")
            ctx = _run(collect_git_context(tmp))
            self.assertIsNotNone(ctx.status)
            self.assertIn("new_file.py", ctx.status)
        clear_git_caches()


class TestFormatGitStatus(unittest.TestCase):
    def test_unavailable(self):
        ctx = GitContextSnapshot(available=False)
        self.assertEqual(format_git_status(ctx), "")

    def test_full_context(self):
        ctx = GitContextSnapshot(
            available=True,
            repo_root="/project",
            branch="feature/ws-5",
            default_branch="main",
            user_name="Test User",
            status="M  src/main.py\n?? new.py",
            recent_commits="abc1234 Initial commit",
        )
        result = format_git_status(ctx)
        self.assertIn("Git repository detected", result)
        self.assertIn("feature/ws-5", result)
        self.assertIn("main", result)
        self.assertIn("Test User", result)
        self.assertIn("M  src/main.py", result)
        self.assertIn("Initial commit", result)

    def test_clean_tree(self):
        ctx = GitContextSnapshot(
            available=True,
            branch="main",
        )
        result = format_git_status(ctx)
        self.assertIn("Working tree clean", result)

    def test_truncated_status(self):
        ctx = GitContextSnapshot(
            available=True,
            branch="main",
            status="M file.py",
            status_truncated=True,
        )
        result = format_git_status(ctx)
        self.assertIn("truncated", result)


if __name__ == "__main__":
    unittest.main()
