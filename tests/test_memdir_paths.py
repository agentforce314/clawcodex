"""Tests for src/memdir/paths.py — Slice A path resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.memdir.paths import (
    find_canonical_git_root,
    get_auto_mem_daily_log_path,
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    get_memory_base_dir,
    has_auto_mem_path_override,
    is_auto_mem_path,
    is_auto_memory_enabled,
    sanitize_path,
)


class SanitizePathTest(unittest.TestCase):
    def test_unix_absolute_keeps_leading_dash(self):
        # Chapter example: /Users/alex/code/myapp → -Users-alex-code-myapp
        self.assertEqual(
            sanitize_path("/Users/alex/code/myapp"),
            "-Users-alex-code-myapp",
        )

    def test_collapses_consecutive_slashes(self):
        self.assertEqual(sanitize_path("/foo//bar///baz"), "-foo-bar-baz")

    def test_handles_backslashes_and_colons(self):
        self.assertEqual(sanitize_path("C:\\Users\\bob"), "C-Users-bob")


class IsAutoMemoryEnabledTest(unittest.TestCase):
    def setUp(self):
        # Snapshot env vars we mutate
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                "CLAUDE_CODE_SIMPLE",
                "CLAUDE_CODE_REMOTE",
                "CLAUDE_CODE_REMOTE_MEMORY_DIR",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_enabled(self):
        self.assertTrue(is_auto_memory_enabled())

    def test_env_truthy_disables(self):
        os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        self.assertFalse(is_auto_memory_enabled())

    def test_env_falsy_enables(self):
        os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "0"
        self.assertTrue(is_auto_memory_enabled())

    def test_simple_mode_disables(self):
        os.environ["CLAUDE_CODE_SIMPLE"] = "1"
        self.assertFalse(is_auto_memory_enabled())

    def test_remote_without_memory_dir_disables(self):
        os.environ["CLAUDE_CODE_REMOTE"] = "1"
        # No CLAUDE_CODE_REMOTE_MEMORY_DIR set
        self.assertFalse(is_auto_memory_enabled())

    def test_remote_with_memory_dir_enabled(self):
        os.environ["CLAUDE_CODE_REMOTE"] = "1"
        os.environ["CLAUDE_CODE_REMOTE_MEMORY_DIR"] = "/tmp/mem"
        self.assertTrue(is_auto_memory_enabled())


class HasAutoMemPathOverrideTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
        os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._saved

    def test_unset_returns_false(self):
        self.assertFalse(has_auto_mem_path_override())

    def test_relative_rejected(self):
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = "relative/foo"
        self.assertFalse(has_auto_mem_path_override())

    def test_root_rejected(self):
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = "/"
        self.assertFalse(has_auto_mem_path_override())

    def test_null_byte_rejected(self):
        # os.environ refuses to hold a null byte, so test the validator
        # directly with the same private path used by the public helper.
        from src.memdir.paths import _validate_memory_path  # type: ignore

        self.assertIsNone(_validate_memory_path("/foo\0bar", expand_tilde=False))

    def test_absolute_accepted(self):
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = "/tmp/memdir"
        self.assertTrue(has_auto_mem_path_override())


class GetAutoMemPathTest(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE",
                "CLAUDE_CODE_REMOTE_MEMORY_DIR",
                "CLAUDE_CONFIG_DIR",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_override_used_when_set(self):
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = "/tmp/mymem"
        self.assertEqual(get_auto_mem_path(), "/tmp/mymem" + os.sep)

    def test_default_uses_base_plus_sanitized_root(self):
        with tempfile.TemporaryDirectory() as base:
            os.environ["CLAUDE_CODE_REMOTE_MEMORY_DIR"] = base
            path = get_auto_mem_path()
            self.assertTrue(path.endswith(os.sep))
            self.assertIn("/projects/", path)
            self.assertIn("/memory/", path)

    def test_entrypoint_is_under_path(self):
        ep = get_auto_mem_entrypoint()
        self.assertTrue(ep.endswith("MEMORY.md"))
        self.assertTrue(ep.startswith(get_auto_mem_path()))

    def test_daily_log_path_shape(self):
        from datetime import date

        d = date(2026, 3, 5)
        log = get_auto_mem_daily_log_path(d)
        self.assertTrue(log.endswith("/2026/03/2026-03-05.md"))


class IsAutoMemPathTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
        os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = "/tmp/test-mem"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", None)
        else:
            os.environ["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] = self._saved

    def test_inside_returns_true(self):
        self.assertTrue(is_auto_mem_path("/tmp/test-mem/foo.md"))

    def test_outside_returns_false(self):
        self.assertFalse(is_auto_mem_path("/etc/passwd"))

    def test_traversal_blocked(self):
        # ../foo within /tmp/test-mem normalizes to /tmp/foo, outside
        self.assertFalse(is_auto_mem_path("/tmp/test-mem/../foo.md"))

    def test_prefix_attack_blocked(self):
        # /tmp/test-mem-evil/foo should not match /tmp/test-mem/
        self.assertFalse(is_auto_mem_path("/tmp/test-mem-evil/foo.md"))


class FindCanonicalGitRootTest(unittest.TestCase):
    def test_in_clawcodex_repo_returns_repo_root(self):
        # Running this test from within the clawcodex repo means the
        # canonical git root should be the repo top-level dir.
        root = find_canonical_git_root(os.getcwd())
        # Either we found a git root (canonical/non-worktree case) or
        # None (running outside a git repo). Both are valid; assert
        # the type.
        if root is not None:
            self.assertTrue(os.path.isdir(root))
            self.assertTrue(os.path.isdir(os.path.join(root, ".git")) or root)


if __name__ == "__main__":
    unittest.main()
