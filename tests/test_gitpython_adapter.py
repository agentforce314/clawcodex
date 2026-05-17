"""Tests for GitPython adapter (Task #4)."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from src.context_system._gitpython_adapter import (
    GitContextSnapshot,
    GitPythonProvider,
    clear_git_caches,
    collect_git_context_with_gitpython,
    format_git_status_with_gitpython,
    is_gitpython_available,
)


class TestGitPythonAvailable:
    def test_gitpython_is_available(self):
        assert is_gitpython_available() is True


class TestGitPythonProvider:
    def test_provider_with_valid_repo(self, tmp_path):
        """Test provider with a valid git repository."""
        # Create a temp git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=tmp_path, capture_output=True)

        provider = GitPythonProvider(cwd=tmp_path)
        assert provider.is_git_repo() is True

    def test_provider_with_invalid_repo(self):
        """Test provider with a non-git directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as non_git_dir:
            provider = GitPythonProvider(cwd=non_git_dir)
            assert provider.is_git_repo() is False


class TestCollectGitContext:
    def test_collect_git_context_valid_repo(self, tmp_path):
        """Test collecting git context from a valid repo."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=tmp_path, capture_output=True)

        clear_git_caches()
        ctx = collect_git_context_with_gitpython(str(tmp_path))
        assert ctx.available is True
        # Branch could be master or main depending on git version
        assert ctx.branch in ("master", "main")

    def test_collect_git_context_invalid_repo(self):
        """Test collecting git context from a non-git directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as non_git_dir:
            clear_git_caches()
            ctx = collect_git_context_with_gitpython(non_git_dir)
            assert ctx.available is False
            assert ctx.error == "Not a git repository"


class TestFormatGitStatus:
    def test_format_git_status_valid(self):
        """Test formatting git status for a valid repo."""
        ctx = GitContextSnapshot(
            available=True,
            branch="feature/test",
            default_branch="main",
            user_name="Test User",
            status=" M file.py",
            status_truncated=False,
            recent_commits="initial commit",
        )
        result = format_git_status_with_gitpython(ctx)
        assert "Git repository detected" in result
        assert "Current branch: feature/test" in result
        assert "Default branch: main" in result
        assert "Test User" in result

    def test_format_git_status_not_available(self):
        """Test formatting git status when repo not available."""
        ctx = GitContextSnapshot(available=False, error="Not a git repository")
        result = format_git_status_with_gitpython(ctx)
        assert result == ""


class TestClearCaches:
    def test_clear_caches(self):
        """Test that clear_git_caches doesn't raise."""
        clear_git_caches()  # Should not raise


class TestBackwardCompatibility:
    def test_returns_git_context_snapshot(self, tmp_path):
        """Ensure adapter returns GitContextSnapshot type."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)

        clear_git_caches()
        ctx = collect_git_context_with_gitpython(str(tmp_path))
        assert isinstance(ctx, GitContextSnapshot)